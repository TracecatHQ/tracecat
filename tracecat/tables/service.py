from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from asyncpg.exceptions import (
    InFailedSQLTransactionError,
    InvalidCachedStatementError,
    UndefinedTableError,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError, NoResultFound, ProgrammingError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import Table, TableColumn
from tracecat.identifiers import TableColumnID, TableID
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.service import BaseService
from tracecat.tables.common import (
    handle_default_value,
    is_valid_sql_type,
    to_sql_clause,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.models import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatNotFoundError

_RETRYABLE_DB_EXCEPTIONS = (
    InvalidCachedStatementError,
    InFailedSQLTransactionError,
)


class BaseTablesService(BaseService):
    """Service for managing user-defined tables."""

    service_name = "tables"

    def _sanitize_identifier(self, identifier: str) -> str:
        """Sanitize table/column names to prevent SQL injection."""
        return sanitize_identifier(identifier)

    def _get_schema_name(self, workspace_id: WorkspaceUUID | None = None) -> str:
        """Generate the schema name for a workspace."""
        ws_id = workspace_id or self._workspace_id()
        # Using double quotes to allow dots in schema name
        return f"tables_{ws_id.short()}"

    def _full_table_name(
        self, table_name: str, workspace_id: WorkspaceUUID | None = None
    ) -> str:
        """Get the full table name for a table."""
        schema_name = self._get_schema_name(workspace_id)
        sanitized_table_name = self._sanitize_identifier(table_name)
        return f'"{schema_name}".{sanitized_table_name}'

    def _workspace_id(self) -> WorkspaceUUID:
        """Get the workspace ID for the current role."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise TracecatAuthorizationError("Workspace ID is required")
        return WorkspaceUUID.new(workspace_id)

    async def list_tables(self) -> Sequence[Table]:
        """List all lookup tables for a workspace.

        Args:
            workspace_id: The ID of the workspace to list tables for

        Returns:
            A sequence of LookupTable objects for the given workspace

        Raises:
            ValueError: If the workspace ID is invalid
        """
        ws_id = self._workspace_id()
        statement = select(Table).where(Table.owner_id == ws_id)
        result = await self.session.exec(statement)
        return result.all()

    async def get_table(self, table_id: TableID) -> Table:
        """Get a lookup table by ID."""
        ws_id = self._workspace_id()
        statement = select(Table).where(
            Table.owner_id == ws_id,
            Table.id == table_id,
        )
        result = await self.session.exec(statement)
        table = result.first()
        if table is None:
            raise TracecatNotFoundError("Table not found")

        return table

    async def get_index(self, table: Table) -> list[str]:
        """Get columns that have unique constraints."""
        schema_name = self._get_schema_name()
        conn = await self.session.connection()

        def inspect_indexes(
            sync_conn: sa.Connection,
        ) -> Sequence[sa.engine.interfaces.ReflectedIndex]:
            inspector = sa.inspect(sync_conn)
            indexes = inspector.get_indexes(table.name, schema=schema_name)
            return indexes

        indexes = await conn.run_sync(inspect_indexes)
        # Assume only single column indexes
        index_names = [
            index["column_names"][0]
            for index in indexes
            if len(index["column_names"]) == 1
            and isinstance(index["column_names"][0], str)
        ]
        self.logger.info("Found unique index column", columns=index_names)
        return index_names

    async def get_table_by_name(self, table_name: str) -> Table:
        """Get a lookup table by name.

        Args:
            table_name: The name of the table to get

        Returns:
            The requested Table

        Raises:
            TracecatNotFoundError: If the table does not exist
        """
        ws_id = self._workspace_id()
        sanitized_name = self._sanitize_identifier(table_name)
        statement = select(Table).where(
            Table.owner_id == ws_id,
            Table.name == sanitized_name,
        )
        result = await self.session.exec(statement)
        table = result.first()
        if table is None:
            raise TracecatNotFoundError(f"Table '{table_name}' not found")
        return table

    @require_access_level(AccessLevel.ADMIN)
    async def create_table(self, params: TableCreate) -> Table:
        """Create a new lookup table.

        Args:
            params: Parameters for creating the table

        Returns:
            The created Table metadata object

        Raises:
            TracecatAuthorizationError: If user lacks required permissions
            ValueError: If table name is invalid
        """
        ws_id = self._workspace_id()
        schema_name = self._get_schema_name(ws_id)
        table_name = self._sanitize_identifier(params.name)

        # Create schema if it doesn't exist
        conn = await self.session.connection()
        await conn.execute(sa.DDL('CREATE SCHEMA IF NOT EXISTS "%s"', schema_name))

        # Define table using SQLAlchemy schema objects
        new_table = sa.Table(
            table_name,
            sa.MetaData(),
            sa.Column(
                "id",
                sa.UUID,
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            schema=schema_name,
        )

        self.logger.info(
            "Creating table", table_name=table_name, schema_name=schema_name
        )

        # Create the physical table
        await conn.run_sync(new_table.create)

        # Create metadata entry
        table = Table(owner_id=ws_id, name=table_name)
        self.session.add(table)
        await self.session.flush()

        return table

    @require_access_level(AccessLevel.ADMIN)
    async def update_table(self, table: Table, params: TableUpdate) -> Table:
        """Update a lookup table."""
        # We need to update the table name in the physical table
        set_fields = params.model_dump(exclude_unset=True)
        if new_name := set_fields.get("name"):
            try:
                conn = await self.session.connection()
                old_full_table_name = self._full_table_name(table.name)
                sanitized_new_name = self._sanitize_identifier(new_name)
                await conn.execute(
                    sa.DDL(
                        "ALTER TABLE %s RENAME TO %s",
                        (old_full_table_name, sanitized_new_name),
                    )
                )
            except ProgrammingError as e:
                self.logger.error(
                    "Error renaming table",
                    error=e,
                    table=table.name,
                    new_name=params.name,
                )
                raise
        # Update DB Table
        for key, value in set_fields.items():
            setattr(table, key, value)

        await self.session.flush()
        return table

    @require_access_level(AccessLevel.ADMIN)
    async def delete_table(self, table: Table) -> None:
        """Delete a lookup table."""
        # Delete the metadata first
        await self.session.delete(table)

        # Drop the actual table
        full_table_name = self._full_table_name(table.name)
        conn = await self.session.connection()
        await conn.execute(sa.DDL("DROP TABLE IF EXISTS %s", full_table_name))
        await self.session.flush()

    """Columns"""

    async def get_column(
        self, table_id: TableID, column_id: TableColumnID
    ) -> TableColumn:
        """Get a column by ID."""
        statement = select(TableColumn).where(
            TableColumn.table_id == table_id,
            TableColumn.id == column_id,
        )
        result = await self.session.exec(statement)
        column = result.first()
        if column is None:
            raise TracecatNotFoundError("Column not found")
        return column

    @require_access_level(AccessLevel.ADMIN)
    async def create_column(
        self, table: Table, params: TableColumnCreate
    ) -> TableColumn:
        """Add a new column to an existing table.

        Args:
            table: The table to add the column to
            params: Parameters for the new column

        Returns:
            The created TableColumn metadata object

        Raises:
            ValueError: If the column type is invalid
        """
        column_name = self._sanitize_identifier(params.name)
        full_table_name = self._full_table_name(table.name)

        # Validate SQL type first
        if not is_valid_sql_type(params.type):
            raise ValueError(f"Invalid type: {params.type}")
        sql_type = SqlType(params.type)

        # Handle default value based on type
        default_value = params.default
        if default_value is not None:
            default_value = handle_default_value(sql_type, default_value)
        # Create the column metadata first
        column = TableColumn(
            table_id=table.id,
            name=column_name,
            type=sql_type.value,
            nullable=params.nullable,
            default=default_value,  # Store original default in metadata
        )
        self.session.add(column)

        # Build the column definition string
        column_def = [f"{column_name} {sql_type.value}"]
        if not params.nullable:
            column_def.append("NOT NULL")
        if default_value is not None:
            column_def.append(f"DEFAULT {default_value}")

        column_def_str = " ".join(column_def)

        # Add the column to the physical table
        conn = await self.session.connection()
        await conn.execute(
            sa.DDL(
                "ALTER TABLE %s ADD COLUMN %s",
                (full_table_name, column_def_str),
            )
        )

        await self.session.flush()
        return column

    @require_access_level(AccessLevel.ADMIN)
    async def update_column(
        self,
        column: TableColumn,
        params: TableColumnUpdate,
    ) -> TableColumn:
        """Update a column in an existing table.

        Args:
            column: The column to update
            params: Parameters for updating the column

        Returns:
            The updated TableColumn metadata object

        Raises:
            ValueError: If the column type is invalid
            ProgrammingError: If the database operation fails
        """
        set_fields = params.model_dump(exclude_unset=True)
        full_table_name = self._full_table_name(column.table.name)
        conn = await self.session.connection()
        is_index = set_fields.pop("is_index", False)

        # Create index if requested
        if is_index:
            await self.create_unique_index(column.table, column.name)

        # Handle physical column changes if name or type is being updated
        if "name" in set_fields or "type" in set_fields:
            old_name = self._sanitize_identifier(column.name)
            new_name = self._sanitize_identifier(set_fields.get("name", column.name))
            new_type = set_fields.get("type", column.type)

            if not is_valid_sql_type(new_type):
                raise ValueError(f"Invalid type: {new_type}")

            # Build ALTER COLUMN statement using safe DDL construction
            if "name" in set_fields:
                await conn.execute(
                    sa.DDL(
                        "ALTER TABLE %s RENAME COLUMN %s TO %s",
                        (full_table_name, old_name, new_name),
                    )
                )
            if "type" in set_fields:
                await conn.execute(
                    sa.DDL(
                        "ALTER TABLE %s ALTER COLUMN %s TYPE %s",
                        (full_table_name, new_name, new_type),
                    )
                )
            if "nullable" in set_fields:
                constraint = (
                    "DROP NOT NULL" if set_fields["nullable"] else "SET NOT NULL"
                )
                await conn.execute(
                    sa.DDL(
                        # SAFE f-string: constraint is a controlled literal string ("DROP NOT NULL" or "SET NOT NULL")
                        # No user input is interpolated here - only predefined SQL keywords
                        f"ALTER TABLE %s ALTER COLUMN %s {constraint}",
                        (full_table_name, new_name),
                    )
                )
            if "default" in set_fields:
                updated_default = set_fields["default"]
                if updated_default is None:
                    await conn.execute(
                        sa.DDL(
                            "ALTER TABLE %s ALTER COLUMN %s DROP DEFAULT",
                            (full_table_name, new_name),
                        )
                    )
                else:
                    # SECURITY NOTE: PostgreSQL DDL does not support parameter binding for DEFAULT clauses.
                    # We must use string interpolation here, but it's SAFE because:
                    # 1. handle_default_value() sanitizes and properly formats the value based on SQL type
                    # 2. It applies proper quoting, escaping, and type casting (e.g., 'value'::text, 123, true)
                    # 3. The function validates the SQL type and rejects invalid inputs
                    # 4. This is the ONLY way to set DEFAULT values in PostgreSQL DDL statements
                    formatted_default = handle_default_value(
                        SqlType(new_type if "type" in set_fields else column.type),
                        updated_default,
                    )
                    await conn.execute(
                        sa.DDL(
                            # SAFE f-string: formatted_default is pre-sanitized by handle_default_value()
                            # Other parameters (table/column names) still use secure parameter binding
                            f"ALTER TABLE %s ALTER COLUMN %s SET DEFAULT {formatted_default}",
                            (full_table_name, new_name),
                        )
                    )

        # Update the column metadata
        for key, value in set_fields.items():
            setattr(column, key, value)

        await self.session.flush()
        return column

    @require_access_level(AccessLevel.ADMIN)
    async def create_unique_index(self, table: Table, column_name: str) -> None:
        """Create a unique index on specified columns."""

        # Check if another index already exists
        index = await self.get_index(table)
        if len(index) > 0:
            raise ValueError("Table cannot have multiple unique indexes")

        # Get the fully qualified table name with schema
        full_table_name = self._full_table_name(table.name)

        # Sanitize column names to prevent SQL injection
        sanitized_column = self._sanitize_identifier(column_name)

        # Create a descriptive name for the index
        # Format: uq_[table_name]_[col1]_[col2]_etc
        index_name = f"uq_{table.name}_{sanitized_column}"

        # Get database connection
        conn = await self.session.connection()

        # Execute the CREATE UNIQUE INDEX SQL command
        await conn.execute(
            sa.DDL(
                "CREATE UNIQUE INDEX %s ON %s (%s)",
                (
                    index_name,  # Name of the index
                    full_table_name,  # Table to create index on
                    sanitized_column,  # Column to index
                ),
            )
        )

        # Commit the transaction
        await self.session.flush()

    @require_access_level(AccessLevel.ADMIN)
    async def delete_column(self, column: TableColumn) -> None:
        """Remove a column from an existing table."""
        full_table_name = self._full_table_name(column.table.name)
        sanitized_column = self._sanitize_identifier(column.name)

        # Delete the column metadata first
        await self.session.delete(column)

        # Drop the column from the physical table using DDL
        conn = await self.session.connection()
        await conn.execute(
            sa.DDL(
                "ALTER TABLE %s DROP COLUMN %s",
                (full_table_name, sanitized_column),
            )
        )

        await self.session.flush()

    """Rows"""

    async def list_rows(
        self, table: Table, *, limit: int = 100, offset: int = 0
    ) -> Sequence[Mapping[str, Any]]:
        """List all rows in a table."""
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(sa.table(sanitized_table_name, schema=schema_name))
            .limit(limit)
            .offset(offset)
        )
        result = await conn.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_row(self, table: Table, row_id: UUID) -> Any:
        """Get a row by ID."""
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(sa.table(sanitized_table_name, schema=schema_name))
            .where(sa.column("id") == row_id)
        )
        result = await conn.execute(stmt)
        row = result.mappings().first()
        if row is None:
            raise TracecatNotFoundError(f"Row {row_id} not found in table {table.name}")
        return row

    async def insert_row(
        self,
        table: Table,
        params: TableRowInsert,
    ) -> dict[str, Any]:
        """Insert a new row into the table.

        Args:
            table: The table to insert into
            params: The row data to insert

        Returns:
            A mapping containing the inserted row data

        Raises:
            ValueError: If conflict keys are specified but not present in the data
            DBAPIError: If there's no unique index on the specified conflict keys
        """
        schema_name = self._get_schema_name()
        conn = await self.session.connection()

        row_data = params.data
        col_map = {c.name: c for c in table.columns}
        upsert = params.upsert

        value_clauses: dict[str, sa.BindParameter] = {}
        cols = []

        table_name_for_logging = table.name
        sanitized_table_name = self._sanitize_identifier(table.name)

        for col, value in row_data.items():
            value_clauses[col] = to_sql_clause(
                value, col_map[col].name, SqlType(col_map[col].type)
            )
            cols.append(sa.column(self._sanitize_identifier(col)))

        if not upsert:
            stmt = (
                sa.insert(sa.table(sanitized_table_name, *cols, schema=schema_name))
                .values(**value_clauses)
                .returning(sa.text("*"))
            )
        else:
            # For upsert operations
            table_obj = sa.table(sanitized_table_name, *cols, schema=schema_name)
            pg_stmt = insert(table_obj)
            pg_stmt = pg_stmt.values(**value_clauses)

            # Get columns with unique constraints for conflict resolution
            index = await self.get_index(table)

            # Check if we have any unique columns to use for conflict resolution
            if not index:
                raise ValueError("Table must have at least one unique index for upsert")

            if len(index) > 1:
                raise ValueError(
                    "Table cannot have multiple unique indexes. This is an unexpected error. Please contact support."
                )

            # Ensure all conflict keys are actually in the data
            if not all(key in value_clauses for key in index):
                raise ValueError("Data to upsert must contain the unique index column")

            # Define what gets updated on conflict
            update_dict = {
                col: pg_stmt.excluded[col]
                for col in value_clauses.keys()
                if col not in index  # Don't update the unique columns
            }

            try:
                # Complete the statement with on_conflict_do_update
                stmt = pg_stmt.on_conflict_do_update(
                    index_elements=index, set_=update_dict
                ).returning(sa.text("*"))

                result = await conn.execute(stmt)
                await self.session.flush()
                row = result.mappings().one()
                return dict(row)
            except ProgrammingError as e:
                # Drill down to the root cause
                original_error = e
                while (cause := e.__cause__) is not None:
                    e = cause
                if "violates unique constraint" in str(e):
                    self.logger.warning(
                        "Trying to insert duplicate values",
                        index=index,
                        table_name=table_name_for_logging,
                    )
                    raise ValueError(
                        "Please check for duplicate values in the unique index columns"
                    ) from original_error
                elif (
                    "no unique or exclusion constraint matching the ON CONFLICT"
                    in str(e)
                ):
                    raise ValueError(
                        "Please check that the unique index columns are present in the data"
                    ) from original_error
                raise

        # For non-upsert or if the exception handling for upsert didn't return
        try:
            result = await conn.execute(stmt)
            await self.session.flush()
            row = result.mappings().one()
            return dict(row)
        except IntegrityError as e:
            # Drill down to the root cause
            original_error = e
            while (cause := e.__cause__) is not None:
                e = cause

            # Check for unique constraint violations (which are the most common IntegrityErrors)
            if "violates unique constraint" in str(e):
                self.logger.warning(
                    "Trying to insert duplicate values",
                    table_name=table_name_for_logging,
                )
                raise ValueError(
                    "Please check for duplicate values"
                ) from original_error
            raise

    async def update_row(
        self,
        table: Table,
        row_id: UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an existing row in the table.

        Args:
            table: The table containing the row to update
            row_id: The ID of the row to update
            data: Dictionary of column names and values to update

        Returns:
            The updated row data

        Raises:
            TracecatNotFoundError: If the row does not exist
        """
        schema_name = self._get_schema_name()
        conn = await self.session.connection()

        # Build update statement using SQLAlchemy
        sanitized_table_name = self._sanitize_identifier(table.name)
        cols = [sa.column(self._sanitize_identifier(k)) for k in data.keys()]
        stmt = (
            sa.update(sa.table(sanitized_table_name, *cols, schema=schema_name))
            .where(sa.column("id") == row_id)
            .values(**data)
            .returning(sa.text("*"))
        )

        result = await conn.execute(stmt)
        await self.session.flush()

        try:
            row = result.mappings().one()
        except NoResultFound:
            raise TracecatNotFoundError(
                f"Row {row_id} not found in table {table.name}"
            ) from None

        return dict(row)

    @require_access_level(AccessLevel.ADMIN)
    async def delete_row(self, table: Table, row_id: UUID) -> None:
        """Delete a row from the table."""
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()
        table_clause = sa.table(sanitized_table_name, schema=schema_name)
        stmt = sa.delete(table_clause).where(sa.column("id") == row_id)
        await conn.execute(stmt)
        await self.session.flush()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_DB_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.2, max=2),
        reraise=True,
    )
    async def lookup_rows(
        self,
        table_name: str,
        *,
        columns: Sequence[str],
        values: Sequence[Any],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lookup a value in a table with automatic retry on database errors."""
        if len(values) != len(columns):
            raise ValueError("Values and column names must have the same length")

        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table_name)

        cols = [sa.column(self._sanitize_identifier(c)) for c in columns]
        stmt = (
            sa.select(sa.text("*"))
            .select_from(sa.table(sanitized_table_name, schema=schema_name))
            .where(
                sa.and_(
                    *[col == value for col, value in zip(cols, values, strict=True)]
                )
            )
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        async with self.session.begin() as txn:
            conn = await txn.session.connection()
            try:
                result = await conn.execute(
                    stmt,
                    execution_options={
                        "isolation_level": "READ COMMITTED",
                    },
                )
                return [dict(row) for row in result.mappings().all()]
            except _RETRYABLE_DB_EXCEPTIONS as e:
                # Log the error for debugging
                self.logger.warning(
                    "Retryable DB exception occurred",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=schema_name,
                )
                # Ensure transaction is rolled back
                await conn.rollback()
                raise
            except ProgrammingError as e:
                while (cause := e.__cause__) is not None:
                    e = cause
                if isinstance(e, UndefinedTableError):
                    raise TracecatNotFoundError(
                        f"Table '{table_name}' does not exist"
                    ) from e
                raise ValueError(str(e)) from e
            except Exception as e:
                self.logger.error(
                    "Unexpected DB exception occurred",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=schema_name,
                )
                raise

    async def search_rows(
        self,
        table: Table,
        *,
        search_term: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search rows in a table with optional text search and filtering.

        Args:
            table: The table to search in
            search_term: Text to search for across all text and JSONB columns
            start_time: Filter records created after this time
            end_time: Filter records created before this time
            updated_before: Filter records updated before this time
            updated_after: Filter records updated after this time
            limit: Maximum number of rows to return
            offset: Number of rows to skip

        Returns:
            List of matching rows as dictionaries

        Raises:
            TracecatNotFoundError: If the table does not exist
            ValueError: If search parameters are invalid
        """
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()

        # Build the base query
        stmt = sa.select(sa.text("*")).select_from(
            sa.table(sanitized_table_name, schema=schema_name)
        )

        # Build WHERE conditions
        where_conditions = []

        # Add text search conditions
        if search_term:
            # Validate search term to prevent abuse
            if len(search_term) > 1000:
                raise ValueError("Search term cannot exceed 1000 characters")
            if "\x00" in search_term:
                raise ValueError("Search term cannot contain null bytes")

            # Get all text-searchable columns (TEXT and JSONB types)
            searchable_columns = [
                col.name
                for col in table.columns
                if col.type in (SqlType.TEXT.value, SqlType.JSONB.value)
            ]

            if searchable_columns:
                # Use SQLAlchemy's concat function for proper parameter binding
                search_pattern = sa.func.concat("%", search_term, "%")
                search_conditions = []
                for col_name in searchable_columns:
                    sanitized_col = self._sanitize_identifier(col_name)
                    if col_name in [
                        c.name for c in table.columns if c.type == SqlType.JSONB.value
                    ]:
                        # For JSONB columns, convert to text for searching
                        search_conditions.append(
                            sa.func.cast(sa.column(sanitized_col), sa.TEXT).ilike(
                                search_pattern
                            )
                        )
                    else:
                        # For TEXT columns, search directly
                        search_conditions.append(
                            sa.column(sanitized_col).ilike(search_pattern)
                        )
                where_conditions.append(sa.or_(*search_conditions))
            else:
                # No searchable columns found, search_term will have no effect
                self.logger.warning(
                    "No searchable columns found for text search",
                    table=table.name,
                    search_term=search_term,
                )

        # Add date filters
        if start_time:
            where_conditions.append(sa.column("created_at") >= start_time)
        if end_time:
            where_conditions.append(sa.column("created_at") <= end_time)
        if updated_after:
            where_conditions.append(sa.column("updated_at") >= updated_after)
        if updated_before:
            where_conditions.append(sa.column("updated_at") <= updated_before)

        # Apply WHERE conditions if any
        if where_conditions:
            stmt = stmt.where(sa.and_(*where_conditions))

        # Apply limit and offset
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset > 0:
            stmt = stmt.offset(offset)

        try:
            result = await conn.execute(stmt)
            return [dict(row) for row in result.mappings().all()]
        except ProgrammingError as e:
            while (cause := e.__cause__) is not None:
                e = cause
            if isinstance(e, UndefinedTableError):
                raise TracecatNotFoundError(
                    f"Table '{table.name}' does not exist"
                ) from e
            raise ValueError(str(e)) from e
        except Exception as e:
            self.logger.error(
                "Unexpected DB exception occurred during search",
                kind=type(e).__name__,
                error=str(e),
                table=table.name,
                schema=schema_name,
            )
            raise

    async def batch_insert_rows(
        self,
        table: Table,
        rows: list[dict[str, Any]],
        *,
        chunk_size: int = 1000,
    ) -> int:
        """Insert multiple rows into the table atomically.

        Args:
            table: The table to insert into
            rows: List of row data to insert
            chunk_size: Maximum number of rows to insert in a single transaction

        Returns:
            Number of rows inserted

        Raises:
            ValueError: If the batch size exceeds the chunk_size
            DBAPIError: If there's a database error during insertion
        """
        if not rows:
            return 0

        if len(rows) > chunk_size:
            raise ValueError(f"Batch size {len(rows)} exceeds maximum of {chunk_size}")

        schema_name = self._get_schema_name()

        # Get all unique column names from the rows
        all_columns = set()
        for row in rows:
            all_columns.update(row.keys())

        # Create sanitized column list
        cols = [sa.column(self._sanitize_identifier(k)) for k in all_columns]
        sanitized_table_name = self._sanitize_identifier(table.name)

        # Start transaction
        conn = await self.session.connection()

        # Build multi-row insert statement without returning clause
        stmt = sa.insert(
            sa.table(sanitized_table_name, *cols, schema=schema_name)
        ).values(rows)

        try:
            # Execute insert and get rowcount directly
            result = await conn.execute(stmt)
            await self.session.flush()
            return result.rowcount
        except Exception as e:
            raise DBAPIError("Failed to insert batch", str(e), e) from e


class TablesService(BaseTablesService):
    """Transactional tables service."""

    async def create_table(self, params: TableCreate) -> Table:
        result = await super().create_table(params)
        await self.session.commit()
        await self.session.refresh(result)
        return result

    async def update_table(self, table: Table, params: TableUpdate) -> Table:
        result = await super().update_table(table, params)
        await self.session.commit()
        await self.session.refresh(result)
        return result

    async def delete_table(self, table: Table) -> None:
        await super().delete_table(table)
        await self.session.commit()

    async def insert_row(self, table: Table, params: TableRowInsert) -> dict[str, Any]:
        result = await super().insert_row(table, params)
        await self.session.commit()
        return result

    async def update_row(
        self, table: Table, row_id: UUID, data: dict[str, Any]
    ) -> dict[str, Any]:
        result = await super().update_row(table, row_id, data)
        await self.session.commit()
        return result

    async def delete_row(self, table: Table, row_id: UUID) -> None:
        await super().delete_row(table, row_id)
        await self.session.commit()

    async def create_column(
        self, table: Table, params: TableColumnCreate
    ) -> TableColumn:
        column = await super().create_column(table, params)
        await self.session.commit()
        await self.session.refresh(column)
        await self.session.refresh(table)
        return column

    async def update_column(
        self, column: TableColumn, params: TableColumnUpdate
    ) -> TableColumn:
        column = await super().update_column(column, params)
        await self.session.commit()
        await self.session.refresh(column)
        return column

    async def delete_column(self, column: TableColumn) -> None:
        await super().delete_column(column)
        await self.session.commit()

    async def batch_insert_rows(
        self, table: Table, rows: list[dict[str, Any]], *, chunk_size: int = 1000
    ) -> int:
        result = await super().batch_insert_rows(table, rows, chunk_size=chunk_size)
        await self.session.commit()
        return result


class TableEditorService(BaseService):
    """Service for editing tables."""

    service_name = "table_editor"

    def __init__(
        self,
        *,
        table_name: str,
        schema_name: str,
        session: AsyncSession,
        role: Role | None = None,
    ):
        super().__init__(session, role)
        self.table_name = sanitize_identifier(table_name)
        self.schema_name = schema_name

    def _full_table_name(self) -> str:
        """Get the full table name for the current role."""
        return f'"{self.schema_name}".{self.table_name}'

    async def get_columns(self) -> Sequence[sa.engine.interfaces.ReflectedColumn]:
        """Get all columns for a table."""

        def inspect_columns(
            sync_conn: sa.Connection,
        ) -> Sequence[sa.engine.interfaces.ReflectedColumn]:
            inspector = sa.inspect(sync_conn)
            return inspector.get_columns(self.table_name, schema=self.schema_name)

        conn = await self.session.connection()
        columns = await conn.run_sync(inspect_columns)
        return columns

    @require_access_level(AccessLevel.ADMIN)
    async def create_column(self, params: TableColumnCreate) -> None:
        """Add a new column to an existing table.

        Args:
            params: Parameters for the new column

        Returns:
            The created TableColumn metadata object

        Raises:
            ValueError: If the column type is invalid
        """

        # Validate SQL type first
        if not is_valid_sql_type(params.type):
            raise ValueError(f"Invalid type: {params.type}")

        # Handle default value based on type
        default_value = params.default
        if default_value is not None:
            default_value = handle_default_value(params.type, default_value)

        # Build the column definition string
        column_def = [f"{params.name} {params.type.value}"]
        if not params.nullable:
            column_def.append("NOT NULL")
        if default_value is not None:
            column_def.append(f"DEFAULT {default_value}")

        column_def_str = " ".join(column_def)

        # Add the column to the physical table
        conn = await self.session.connection()
        await conn.execute(
            sa.DDL(
                "ALTER TABLE %s ADD COLUMN %s",
                (self._full_table_name(), column_def_str),
            )
        )

        await self.session.flush()

    @require_access_level(AccessLevel.ADMIN)
    async def update_column(self, column_name: str, params: TableColumnUpdate) -> None:
        """Update a column in an existing table.

        Args:
            params: Parameters for updating the column

        Returns:
            The updated TableColumn metadata object

        Raises:
            ValueError: If the column type is invalid
            ProgrammingError: If the database operation fails
        """
        set_fields = params.model_dump(exclude_unset=True)
        conn = await self.session.connection()

        new_name = column_name
        full_table_name = self._full_table_name()

        # Execute ALTER statements using safe DDL construction
        if "name" in set_fields:
            new_name = sanitize_identifier(set_fields["name"])
            await conn.execute(
                sa.DDL(
                    "ALTER TABLE %s RENAME COLUMN %s TO %s",
                    (full_table_name, column_name, new_name),
                )
            )
        if "type" in set_fields:
            new_type = set_fields["type"]
            await conn.execute(
                sa.DDL(
                    "ALTER TABLE %s ALTER COLUMN %s TYPE %s",
                    (full_table_name, new_name, new_type),
                )
            )
        if "nullable" in set_fields:
            constraint = "DROP NOT NULL" if set_fields["nullable"] else "SET NOT NULL"
            await conn.execute(
                sa.DDL(
                    # SAFE f-string: constraint is a controlled literal string ("DROP NOT NULL" or "SET NOT NULL")
                    # No user input is interpolated here - only predefined SQL keywords
                    f"ALTER TABLE %s ALTER COLUMN %s {constraint}",
                    (full_table_name, new_name),
                )
            )
        if "default" in set_fields:
            updated_default = set_fields["default"]
            if updated_default is None:
                await conn.execute(
                    sa.DDL(
                        "ALTER TABLE %s ALTER COLUMN %s DROP DEFAULT",
                        (full_table_name, new_name),
                    )
                )
            else:
                # SECURITY NOTE: PostgreSQL DDL does not support parameter binding for DEFAULT clauses.
                # We must use string interpolation here, but it's SAFE because:
                # 1. handle_default_value() sanitizes and properly formats the value based on SQL type
                # 2. It applies proper quoting, escaping, and type casting (e.g., 'value'::text, 123, true)
                # 3. The function validates the SQL type and rejects invalid inputs
                # 4. This is the ONLY way to set DEFAULT values in PostgreSQL DDL statements
                formatted_default = handle_default_value(
                    SqlType(set_fields.get("type", "TEXT")), updated_default
                )
                await conn.execute(
                    sa.DDL(
                        # SAFE f-string: formatted_default is pre-sanitized by handle_default_value()
                        # Other parameters (table/column names) still use secure parameter binding
                        f"ALTER TABLE %s ALTER COLUMN %s SET DEFAULT {formatted_default}",
                        (full_table_name, new_name),
                    )
                )

        await self.session.flush()

    @require_access_level(AccessLevel.ADMIN)
    async def delete_column(self, column_name: str) -> None:
        """Remove a column from an existing table."""
        sanitized_column = sanitize_identifier(column_name)

        # Drop the column from the physical table using DDL
        conn = await self.session.connection()
        await conn.execute(
            sa.DDL(
                "ALTER TABLE %s DROP COLUMN %s",
                (self._full_table_name(), sanitized_column),
            )
        )

        await self.session.flush()

    async def list_rows(
        self, *, limit: int = 100, offset: int = 0
    ) -> Sequence[Mapping[str, Any]]:
        """List all rows in a table."""
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(sa.table(self.table_name, schema=self.schema_name))
            .limit(limit)
            .offset(offset)
        )
        result = await conn.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_row(self, row_id: UUID) -> dict[str, Any]:
        """Get a row by ID."""
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(sa.table(self.table_name, schema=self.schema_name))
            .where(sa.column("id") == row_id)
        )
        result = await conn.execute(stmt)
        row = result.mappings().first()
        if row is None:
            raise TracecatNotFoundError(
                f"Row {row_id} not found in table {self.table_name}"
            )
        return dict(row)

    async def insert_row(self, params: TableRowInsert) -> dict[str, Any]:
        """Insert a new row into the table.

        Args:
            params: The row data to insert

        Returns:
            A mapping containing the inserted row data
        """
        conn = await self.session.connection()

        row_data = params.data
        col_map = {c["name"]: c for c in await self.get_columns()}

        value_clauses: dict[str, sa.BindParameter] = {}
        cols = []
        for col, value in row_data.items():
            value_clauses[col] = sa.bindparam(col, value, type_=col_map[col]["type"])
            cols.append(sa.column(sanitize_identifier(col)))

        stmt = (
            sa.insert(sa.table(self.table_name, *cols, schema=self.schema_name))
            .values(**value_clauses)
            .returning(sa.text("*"))
        )
        result = await conn.execute(stmt)
        await self.session.flush()
        row = result.mappings().one()
        return dict(row)

    async def update_row(self, row_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing row in the table.

        Args:
            row_id: The ID of the row to update
            data: Dictionary of column names and values to update

        Returns:
            The updated row data

        Raises:
            TracecatNotFoundError: If the row does not exist
        """
        conn = await self.session.connection()

        # Build update statement using SQLAlchemy
        cols = [sa.column(sanitize_identifier(k)) for k in data.keys()]
        stmt = (
            sa.update(sa.table(self.table_name, *cols, schema=self.schema_name))
            .where(sa.column("id") == row_id)
            .values(**data)
            .returning(sa.text("*"))
        )

        result = await conn.execute(stmt)
        await self.session.flush()

        try:
            row = result.mappings().one()
        except NoResultFound:
            raise TracecatNotFoundError(
                f"Row {row_id} not found in table {self.table_name}"
            ) from None

        return dict(row)

    @require_access_level(AccessLevel.ADMIN)
    async def delete_row(self, row_id: UUID) -> None:
        """Delete a row from the table."""
        conn = await self.session.connection()
        table_clause = sa.table(self.table_name, schema=self.schema_name)
        stmt = sa.delete(table_clause).where(sa.column("id") == row_id)
        await conn.execute(stmt)
        await self.session.flush()


def sanitize_identifier(identifier: str) -> str:
    """Sanitize table/column names to prevent SQL injection."""
    # Remove any non-alphanumeric characters except underscores
    sanitized = "".join(c for c in identifier if c.isalnum() or c == "_")
    if not sanitized[0].isalpha():
        raise ValueError("Identifier must start with a letter")
    return sanitized.lower()
