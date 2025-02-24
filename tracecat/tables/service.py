from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from asyncpg.exceptions import (
    InFailedSQLTransactionError,
    InvalidCachedStatementError,
)
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlmodel import select
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
from tracecat.logger import logger
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
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatNotFoundError

_RETRYABLE_DB_EXCEPTIONS = (
    InvalidCachedStatementError,
    InFailedSQLTransactionError,
    DBAPIError,
)


class TablesService(BaseService):
    """Service for managing user-defined tables."""

    service_name = "tables"

    def _sanitize_identifier(self, identifier: str) -> str:
        """Sanitize table/column names to prevent SQL injection."""
        # Remove any non-alphanumeric characters except underscores
        sanitized = "".join(c for c in identifier if c.isalnum() or c == "_")
        if not sanitized[0].isalpha():
            raise ValueError("Identifier must start with a letter")
        return sanitized.lower()

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
        return f'"{schema_name}".{table_name}'

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

        logger.info("Creating table", table_name=table_name, schema_name=schema_name)

        # Create the physical table
        await conn.run_sync(new_table.create)

        # Create metadata entry
        metadata = Table(owner_id=ws_id, name=table_name)
        self.session.add(metadata)
        await self.session.commit()
        await self.session.refresh(metadata)

        return metadata

    @require_access_level(AccessLevel.ADMIN)
    async def update_table(self, table: Table, params: TableUpdate) -> Table:
        """Update a lookup table."""
        # We need to update the table name in the physical table
        set_fields = params.model_dump(exclude_unset=True)
        if new_name := set_fields.get("name"):
            try:
                conn = await self.session.connection()
                old_full_table_name = self._full_table_name(table.name)
                await conn.execute(
                    sa.DDL(
                        "ALTER TABLE %s RENAME TO %s",
                        (old_full_table_name, new_name),
                    )
                )
            except ProgrammingError as e:
                logger.error(
                    "Error renaming table",
                    error=e,
                    table=table.name,
                    new_name=params.name,
                )
                raise
        # Update DB Table
        for key, value in set_fields.items():
            setattr(table, key, value)

        await self.session.commit()
        await self.session.refresh(table)
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
        await self.session.commit()

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

        await self.session.commit()
        await self.session.refresh(column)
        await self.session.refresh(table)
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

        # Handle physical column changes if name or type is being updated
        if "name" in set_fields or "type" in set_fields:
            old_name = self._sanitize_identifier(column.name)
            new_name = self._sanitize_identifier(set_fields.get("name", column.name))
            new_type = set_fields.get("type", column.type)

            if not is_valid_sql_type(new_type):
                raise ValueError(f"Invalid type: {new_type}")

            # Build ALTER COLUMN statement
            alter_stmts = []
            if "name" in set_fields:
                alter_stmts.append(f"RENAME COLUMN {old_name} TO {new_name}")
            if "type" in set_fields:
                alter_stmts.append(f"ALTER COLUMN {new_name} TYPE {new_type}")
            if "nullable" in set_fields:
                constraint = (
                    "DROP NOT NULL" if set_fields["nullable"] else "SET NOT NULL"
                )
                alter_stmts.append(f'ALTER COLUMN "{new_name}" {constraint}')
            if "default" in set_fields:
                updated_default = set_fields["default"]
                if updated_default is None:
                    alter_stmts.append(f'ALTER COLUMN "{new_name}" DROP DEFAULT')
                else:
                    alter_stmts.append(
                        f"ALTER COLUMN \"{new_name}\" SET DEFAULT '{updated_default}'"
                    )

            # Execute all ALTER statements
            logger.info("Updating column", stmts=alter_stmts)
            for stmt in alter_stmts:
                await conn.execute(sa.DDL(f"ALTER TABLE {full_table_name} {stmt}"))

        # Update the column metadata
        for key, value in set_fields.items():
            setattr(column, key, value)

        await self.session.commit()
        await self.session.refresh(column)
        return column

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

        await self.session.commit()

    """Rows"""

    async def list_rows(
        self, table: Table, *, limit: int = 100, offset: int = 0
    ) -> Sequence[Mapping[str, Any]]:
        """List all rows in a table."""
        schema_name = self._get_schema_name()
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(sa.table(table.name, schema=schema_name))
            .limit(limit)
            .offset(offset)
        )
        result = await conn.execute(stmt)
        return [dict(row) for row in result.mappings().all()]

    async def get_row(self, table: Table, row_id: UUID) -> Any:
        """Get a row by ID."""
        schema_name = self._get_schema_name()
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(sa.table(table.name, schema=schema_name))
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
    ) -> Mapping[str, Any] | None:
        """Insert a new row into the table.

        Args:
            table: The table to insert into
            params: The row data to insert

        Returns:
            A mapping containing the inserted row data
        """
        schema_name = self._get_schema_name()
        conn = await self.session.connection()

        row_data = params.data
        col_map = {c.name: c for c in table.columns}

        value_clauses: dict[str, sa.TextClause] = {}
        cols = []
        for col, value in row_data.items():
            value_clauses[col] = to_sql_clause(value, col_map[col])
            cols.append(sa.column(self._sanitize_identifier(col)))

        stmt = (
            sa.insert(sa.table(table.name, *cols, schema=schema_name))
            .values(**value_clauses)
            .returning(sa.text("*"))
        )
        result = await conn.execute(stmt)
        await self.session.commit()
        row = result.mappings().one()
        return dict(row)

    async def update_row(
        self,
        table: Table,
        row_id: UUID,
        data: dict[str, Any],
    ) -> Mapping[str, Any]:
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
        cols = [sa.column(self._sanitize_identifier(k)) for k in data.keys()]
        stmt = (
            sa.update(sa.table(table.name, *cols, schema=schema_name))
            .where(sa.column("id") == row_id)
            .values(**data)
            .returning(sa.text("*"))
        )

        result = await conn.execute(stmt)
        await self.session.commit()

        try:
            row = result.mappings().one()
        except sa.exc.NoResultFound:
            raise TracecatNotFoundError(
                f"Row {row_id} not found in table {table.name}"
            ) from None

        return dict(row)

    @require_access_level(AccessLevel.ADMIN)
    async def delete_row(self, table: Table, row_id: UUID) -> None:
        """Delete a row from the table."""
        schema_name = self._get_schema_name()
        conn = await self.session.connection()
        table_clause = sa.table(table.name, schema=schema_name)
        stmt = sa.delete(table_clause).where(sa.column("id") == row_id)
        await conn.execute(stmt)
        await self.session.commit()

    "Lookups"

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_DB_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.2, max=2),
        reraise=True,
    )
    async def lookup_row(
        self,
        table_name: str,
        *,
        columns: Sequence[str],
        values: Sequence[Any],
    ) -> Sequence[Mapping[str, Any]]:
        """Lookup a value in a table with automatic retry on database errors."""
        if len(values) != len(columns):
            raise ValueError("Values and column names must have the same length")

        schema_name = self._get_schema_name()

        cols = [sa.column(self._sanitize_identifier(c)) for c in columns]
        stmt = (
            sa.select(sa.text("*"))
            .select_from(sa.table(table_name, schema=schema_name))
            .where(
                sa.and_(
                    *[col == value for col, value in zip(cols, values, strict=True)]
                )
            )
        )
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
                logger.warning(
                    "Retryable DB exception occurred",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=schema_name,
                )
                # Ensure transaction is rolled back
                await conn.rollback()
                raise
            except Exception as e:
                logger.error(
                    "Unexpected DB exception occurred",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
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

        # Start transaction
        conn = await self.session.connection()

        # Build multi-row insert
        # Build multi-row insert statement without returning clause
        stmt = sa.insert(sa.table(table.name, *cols, schema=schema_name)).values(rows)

        try:
            # Execute insert and get rowcount directly
            result = await conn.execute(stmt)
            await self.session.commit()
            return result.rowcount
        except Exception as e:
            # Let the transaction context manager handle rollback
            raise DBAPIError("Failed to insert batch", str(e), e) from e
