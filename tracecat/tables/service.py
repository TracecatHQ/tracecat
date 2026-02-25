import csv
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum
from io import StringIO
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from asyncpg.exceptions import (
    InFailedSQLTransactionError,
    InvalidCachedStatementError,
    UndefinedTableError,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import DBAPIError, IntegrityError, NoResultFound, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import Table, TableColumn
from tracecat.exceptions import (
    TracecatImportError,
    TracecatNotFoundError,
)
from tracecat.identifiers import TableColumnID, TableID
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.logger import logger
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tables.common import (
    coerce_multi_select_value,
    coerce_select_value,
    coerce_to_date,
    coerce_to_utc_datetime,
    convert_value,
    handle_default_value,
    is_valid_sql_type,
    normalize_column_options,
    to_sql_clause,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import (
    CSVSchemaInferer,
    InferredCSVColumn,
    generate_table_name,
)
from tracecat.tables.schemas import (
    TableAggregation,
    TableAggregationBucket,
    TableAggregationRead,
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableSearchResponse,
    TableUpdate,
)

_RETRYABLE_DB_EXCEPTIONS = (
    InvalidCachedStatementError,
    InFailedSQLTransactionError,
)

_NUMERIC_SQL_TYPES = frozenset({SqlType.INTEGER.value, SqlType.NUMERIC.value})
_LOOKUP_FIXED_COLUMNS = {"id", "created_at", "updated_at"}


def _normalize_aggregation_value(
    value: Any,
) -> int | float | str | bool | datetime | UUID | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, int | float | str | bool | datetime | UUID):
        return value
    return str(value)


class BaseTablesService(BaseWorkspaceService):
    """Service for managing user-defined tables."""

    service_name = "tables"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.ws_uuid = WorkspaceUUID.new(self.workspace_id)

    def _sanitize_identifier(self, identifier: str) -> str:
        """Sanitize table/column names to prevent SQL injection."""
        return sanitize_identifier(identifier)

    def _get_schema_name(self, workspace_id: WorkspaceUUID | None = None) -> str:
        """Generate the schema name for a workspace."""
        ws_id = workspace_id or self.ws_uuid
        # Using double quotes to allow dots in schema name
        return f"tables_{ws_id.short()}"

    def _full_table_name(
        self, table_name: str, workspace_id: WorkspaceUUID | None = None
    ) -> str:
        """Get the full table name for a table."""
        schema_name = self._get_schema_name(workspace_id)
        sanitized_table_name = self._sanitize_identifier(table_name)
        return f'"{schema_name}".{sanitized_table_name}'

    async def _find_unique_table_name(self, base_name: str) -> str:
        """Find a unique table name by appending numeric suffixes if required."""
        candidate = base_name
        suffix = 1
        while True:
            try:
                await self.get_table_by_name(candidate)
            except TracecatNotFoundError:
                return candidate
            candidate = f"{base_name}_{suffix}"
            suffix += 1

    def _normalize_options_for_type(
        self, sql_type: SqlType, options: list[str] | None
    ) -> list[str] | None:
        # Only SELECT and MULTI_SELECT types support options
        if sql_type not in (SqlType.SELECT, SqlType.MULTI_SELECT):
            if options:
                raise ValueError(
                    "Options are only supported for SELECT or MULTI_SELECT"
                )
            return None

        # For SELECT/MULTI_SELECT, normalize and validate options
        normalized = normalize_column_options(options)
        if not normalized:
            raise ValueError(
                "SELECT and MULTI_SELECT columns must define at least one option"
            )
        return normalized

    def _coerce_value_for_column(
        self, sql_type: SqlType, value: Any, options: list[str] | None
    ) -> Any:
        if value is None:
            return None
        if sql_type is SqlType.SELECT:
            return coerce_select_value(value, options=options)
        if sql_type is SqlType.MULTI_SELECT:
            return coerce_multi_select_value(value, options=options)
        return value

    def _normalize_row_inputs(
        self, table: Table, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Coerce row inputs to the expected SQL types."""
        if not data:
            return {}

        column_index = {column.name: column for column in table.columns}
        normalised: dict[str, Any] = {}
        for column_name, value in data.items():
            column = column_index.get(column_name)
            if column is None:
                raise ValueError(
                    f"Column '{column_name}' does not exist in table '{table.name}'"
                )

            sql_type = SqlType(column.type)
            if value is None:
                normalised[column_name] = None
                continue

            if sql_type in (SqlType.SELECT, SqlType.MULTI_SELECT):
                normalised[column_name] = self._coerce_value_for_column(
                    sql_type, value, column.options
                )
                continue

            if sql_type in {SqlType.TIMESTAMP, SqlType.TIMESTAMPTZ}:
                normalised[column_name] = coerce_to_utc_datetime(value)
            elif sql_type is SqlType.DATE and value is not None:
                normalised[column_name] = coerce_to_date(value)
            else:
                normalised[column_name] = value

        return normalised

    def _sa_type_for_column(self, sql_type: SqlType) -> sa.types.TypeEngine:
        """Map SqlType to SQLAlchemy column types for safe binding."""
        match sql_type:
            case SqlType.TEXT | SqlType.SELECT:
                return sa.String()
            case SqlType.INTEGER:
                return sa.BigInteger()
            case SqlType.NUMERIC:
                return sa.Numeric()
            case SqlType.DATE:
                return sa.Date()
            case SqlType.BOOLEAN:
                return sa.Boolean()
            case SqlType.TIMESTAMP | SqlType.TIMESTAMPTZ:
                return sa.TIMESTAMP(timezone=True)
            case SqlType.JSONB | SqlType.MULTI_SELECT:
                return JSONB()
            case SqlType.UUID:
                return sa.UUID()
            case _:
                return sa.String()

    async def list_tables(self) -> Sequence[Table]:
        """List all lookup tables for a workspace.

        Args:
            workspace_id: The ID of the workspace to list tables for

        Returns:
            A sequence of LookupTable objects for the given workspace

        Raises:
            ValueError: If the workspace ID is invalid
        """
        statement = select(Table).where(Table.workspace_id == self.ws_uuid)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_table(self, table_id: TableID) -> Table:
        """Get a lookup table by ID."""
        statement = select(Table).where(
            Table.workspace_id == self.ws_uuid,
            Table.id == table_id,
        )
        result = await self.session.execute(statement)
        table = result.scalars().first()
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
        sanitized_name = self._sanitize_identifier(table_name)
        statement = select(Table).where(
            Table.workspace_id == self.ws_uuid,
            Table.name == sanitized_name,
        )
        result = await self.session.execute(statement)
        table = result.scalars().first()
        if table is None:
            raise TracecatNotFoundError(f"Table '{table_name}' not found")
        return table

    @audit_log(resource_type="table", action="create")
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
        schema_name = self._get_schema_name(self.ws_uuid)
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
        table = Table(workspace_id=self.ws_uuid, name=table_name)
        self.session.add(table)
        await self.session.flush()

        # Create columns if specified
        # Call base class method directly to avoid per-column commits
        for col_params in params.columns:
            await BaseTablesService.create_column(self, table, col_params)

        return table

    @audit_log(resource_type="table", action="update")
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

    @audit_log(resource_type="table", action="delete")
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
        result = await self.session.execute(statement)
        column = result.scalars().first()
        if column is None:
            raise TracecatNotFoundError("Column not found")
        return column

    @audit_log(resource_type="table_column", action="create")
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
        normalized_options = self._normalize_options_for_type(sql_type, params.options)

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
            options=normalized_options,
        )
        self.session.add(column)

        # Build the column definition string
        # Map SELECT -> TEXT, MULTI_SELECT -> JSONB for physical storage
        if sql_type is SqlType.SELECT:
            column_type_sql = SqlType.TEXT.value
        elif sql_type is SqlType.MULTI_SELECT:
            column_type_sql = SqlType.JSONB.value
        else:
            # Map INTEGER to BIGINT for larger integer support
            column_type_sql = (
                "BIGINT" if sql_type == SqlType.INTEGER else sql_type.value
            )
        column_def = [f"{column_name} {column_type_sql}"]
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

    @audit_log(resource_type="table_column", action="update")
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
        requested_options = set_fields.pop("options", None)

        # Create index if requested
        if is_index:
            await self.create_unique_index(column.table, column.name)

        # Handle options for SELECT/MULTI_SELECT columns
        target_type = (
            SqlType(set_fields["type"])
            if "type" in set_fields
            else SqlType(column.type)
        )
        if requested_options is not None:
            normalized_options = self._normalize_options_for_type(
                target_type, requested_options
            )
            set_fields["options"] = normalized_options
        elif "type" in set_fields:
            if (
                target_type in (SqlType.SELECT, SqlType.MULTI_SELECT)
                and not column.options
            ):
                raise ValueError(
                    "SELECT and MULTI_SELECT columns must define at least one option"
                )
            elif target_type not in (SqlType.SELECT, SqlType.MULTI_SELECT):
                set_fields["options"] = None

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
                # Map SELECT -> TEXT, MULTI_SELECT -> JSONB for physical storage
                if target_type is SqlType.SELECT:
                    physical_type = SqlType.TEXT.value
                elif target_type is SqlType.MULTI_SELECT:
                    physical_type = SqlType.JSONB.value
                else:
                    physical_type = (
                        "BIGINT" if SqlType(new_type) == SqlType.INTEGER else new_type
                    )
                await conn.execute(
                    sa.DDL(
                        "ALTER TABLE %s ALTER COLUMN %s TYPE %s",
                        (full_table_name, new_name, physical_type),
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

    @audit_log(resource_type="table_column", action="delete")
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

        row_data = self._normalize_row_inputs(table, params.data)
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

        # Normalise inputs and build update statement using SQLAlchemy
        normalised_data = self._normalize_row_inputs(table, data)
        col_map = {c.name: c for c in table.columns}
        sanitized_table_name = self._sanitize_identifier(table.name)
        value_clauses: dict[str, sa.BindParameter] = {}
        cols = []
        for column_name, value in normalised_data.items():
            cols.append(sa.column(self._sanitize_identifier(column_name)))
            value_clauses[column_name] = to_sql_clause(
                value, col_map[column_name].name, SqlType(col_map[column_name].type)
            )

        stmt = (
            sa.update(sa.table(sanitized_table_name, *cols, schema=schema_name))
            .where(sa.column("id") == row_id)
            .values(**value_clauses)
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

    async def delete_row(self, table: Table, row_id: UUID) -> None:
        """Delete a row from the table."""
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()
        table_clause = sa.table(sanitized_table_name, schema=schema_name)
        stmt = sa.delete(table_clause).where(sa.column("id") == row_id)
        await conn.execute(stmt)
        await self.session.flush()

    async def batch_delete_rows(self, table: Table, row_ids: list[UUID]) -> int:
        """Delete multiple rows from the table.

        Args:
            table: The table containing the rows to delete
            row_ids: List of row IDs to delete

        Returns:
            Number of rows deleted
        """
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()
        table_clause = sa.table(sanitized_table_name, schema=schema_name)
        stmt = sa.delete(table_clause).where(sa.column("id").in_(row_ids))
        result = await conn.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def batch_update_rows(
        self, table: Table, row_ids: list[UUID], data: dict[str, Any]
    ) -> int:
        """Update multiple rows in the table with the same data.

        Args:
            table: The table containing the rows to update
            row_ids: List of row IDs to update
            data: Dictionary of column names and values to set

        Returns:
            Number of rows updated
        """
        schema_name = self._get_schema_name()
        conn = await self.session.connection()

        normalised_data = self._normalize_row_inputs(table, data)
        col_map = {c.name: c for c in table.columns}
        sanitized_table_name = self._sanitize_identifier(table.name)
        value_clauses: dict[str, sa.BindParameter] = {}
        cols = []
        for column_name, value in normalised_data.items():
            cols.append(sa.column(self._sanitize_identifier(column_name)))
            value_clauses[column_name] = to_sql_clause(
                value, col_map[column_name].name, SqlType(col_map[column_name].type)
            )

        stmt = (
            sa.update(sa.table(sanitized_table_name, *cols, schema=schema_name))
            .where(sa.column("id").in_(row_ids))
            .values(**value_clauses)
        )

        result = await conn.execute(stmt)
        await self.session.flush()
        return result.rowcount

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

        table = await self.get_table_by_name(table_name)
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table_name)
        table_clause = sa.table(sanitized_table_name, schema=schema_name)

        field_lookup = self._build_field_lookup(table)

        resolved_columns = [
            self._resolve_lookup_field_name(column_name, field_lookup)
            for column_name in columns
        ]
        where_cols = [
            sa.column(self._sanitize_identifier(name)) for name in resolved_columns
        ]
        where_clause = sa.and_(
            *[col == value for col, value in zip(where_cols, values, strict=True)]
        )

        stmt = sa.select(sa.text("*")).select_from(table_clause).where(where_clause)
        if limit is not None:
            stmt = stmt.limit(limit)

        txn_cm = (
            self.session.begin_nested()
            if self.session.in_transaction()
            else self.session.begin()
        )
        async with txn_cm as txn:
            conn = await txn.session.connection()
            try:
                result = await conn.execute(
                    stmt,
                    execution_options={
                        "isolation_level": "READ COMMITTED",
                    },
                )
                rows = [dict(row) for row in result.mappings().all()]
                return rows
            except _RETRYABLE_DB_EXCEPTIONS as e:
                # Log the error for debugging
                # Note: Context manager handles rollback (savepoint or full) automatically
                self.logger.warning(
                    "Retryable DB exception occurred",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=schema_name,
                )
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

    def _build_field_lookup(self, table: Table) -> dict[str, str]:
        field_lookup = {column.name.lower(): column.name for column in table.columns}
        for fixed_col in _LOOKUP_FIXED_COLUMNS:
            field_lookup[fixed_col] = fixed_col
        return field_lookup

    def _resolve_lookup_field_name(
        self, field_name: str, field_lookup: dict[str, str]
    ) -> str:
        resolved = field_lookup.get(field_name.lower())
        if resolved is None:
            raise ValueError(f"Unknown lookup field: {field_name}")
        return resolved

    def _is_numeric_lookup_field(self, table: Table, field_name: str | None) -> bool:
        if field_name is None:
            return False
        for column in table.columns:
            if column.name == field_name:
                return column.type in _NUMERIC_SQL_TYPES
        return False

    def _build_search_where_conditions(
        self,
        *,
        table: Table,
        search_term: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
    ) -> list[ColumnElement[bool]]:
        where_conditions: list[ColumnElement[bool]] = []

        if search_term:
            if len(search_term) > 1000:
                raise ValueError("Search term cannot exceed 1000 characters")
            if "\x00" in search_term:
                raise ValueError("Search term cannot contain null bytes")

            searchable_columns = [
                col.name
                for col in table.columns
                if col.type
                in (
                    SqlType.TEXT.value,
                    SqlType.JSONB.value,
                    SqlType.SELECT.value,
                    SqlType.MULTI_SELECT.value,
                )
            ]
            json_searchable_columns = {
                col.name
                for col in table.columns
                if col.type in (SqlType.JSONB.value, SqlType.MULTI_SELECT.value)
            }

            if searchable_columns:
                search_pattern = sa.func.concat("%", search_term, "%")
                search_conditions: list[ColumnElement[bool]] = []
                for col_name in searchable_columns:
                    sanitized_col = self._sanitize_identifier(col_name)
                    if col_name in json_searchable_columns:
                        search_conditions.append(
                            sa.func.cast(sa.column(sanitized_col), sa.TEXT).ilike(
                                search_pattern
                            )
                        )
                    else:
                        search_conditions.append(
                            sa.column(sanitized_col).ilike(search_pattern)
                        )
                where_conditions.append(sa.or_(*search_conditions))
            else:
                self.logger.warning(
                    "No searchable columns found for text search",
                    table=table.name,
                    search_term=search_term,
                )

        if start_time is not None:
            where_conditions.append(sa.column("created_at") >= start_time)
        if end_time is not None:
            where_conditions.append(sa.column("created_at") <= end_time)
        if updated_after is not None:
            where_conditions.append(sa.column("updated_at") >= updated_after)
        if updated_before is not None:
            where_conditions.append(sa.column("updated_at") <= updated_before)

        return where_conditions

    def _build_aggregation_stmt(
        self,
        *,
        table: Table,
        table_clause: Any,
        where_conditions: Sequence[ColumnElement[bool]],
        agg: TableAggregation,
        agg_field: str | None,
        group_by: str | None,
        field_lookup: dict[str, str],
    ) -> sa.Select[Any]:
        if agg is TableAggregation.SUM:
            selected_field = agg_field
        elif agg is TableAggregation.VALUE_COUNTS:
            selected_field = None
        else:
            selected_field = agg_field or group_by
        selected_col: ColumnElement[Any] | None = None
        if selected_field is not None:
            resolved_selected_field = self._resolve_lookup_field_name(
                selected_field, field_lookup
            )
            selected_field = resolved_selected_field
            selected_col = sa.column(self._sanitize_identifier(resolved_selected_field))

        if agg in {TableAggregation.MEAN, TableAggregation.MEDIAN}:
            if not self._is_numeric_lookup_field(table, selected_field):
                raise ValueError(f"{agg.value} aggregation requires numeric agg_field")

        if (
            agg
            in {
                TableAggregation.MIN,
                TableAggregation.MAX,
                TableAggregation.MODE,
                TableAggregation.N_UNIQUE,
            }
            and selected_col is None
        ):
            raise ValueError(f"{agg.value} aggregation requires agg_field or group_by")

        if agg is TableAggregation.SUM:
            if agg_field is not None and not self._is_numeric_lookup_field(
                table, selected_field
            ):
                raise ValueError(
                    "sum aggregation requires numeric agg_field, or omit agg_field to count rows"
                )

        agg_expr = self._aggregation_expression(agg=agg, selected_col=selected_col)

        stmt = sa.select(agg_expr.label("agg_value")).select_from(table_clause)
        if where_conditions:
            stmt = stmt.where(sa.and_(*where_conditions))

        if group_by is None:
            return stmt

        resolved_group_by = self._resolve_lookup_field_name(group_by, field_lookup)
        group_col = sa.column(self._sanitize_identifier(resolved_group_by))
        group_stmt = (
            sa.select(
                group_col.label("group_value"),
                agg_expr.label("agg_value"),
            )
            .select_from(table_clause)
            .group_by(group_col)
            .order_by(group_col)
        )
        if where_conditions:
            group_stmt = group_stmt.where(sa.and_(*where_conditions))
        return group_stmt

    def _aggregation_expression(
        self,
        *,
        agg: TableAggregation,
        selected_col: ColumnElement[Any] | None,
    ) -> ColumnElement[Any]:
        if agg is TableAggregation.SUM:
            if selected_col is None:
                return func.count(sa.column("id"))
            return func.coalesce(func.sum(sa.cast(selected_col, sa.Float)), 0)
        if agg is TableAggregation.MIN:
            assert selected_col is not None
            return func.min(selected_col)
        if agg is TableAggregation.MAX:
            assert selected_col is not None
            return func.max(selected_col)
        if agg is TableAggregation.MEAN:
            assert selected_col is not None
            return func.avg(sa.cast(selected_col, sa.Float))
        if agg is TableAggregation.MEDIAN:
            assert selected_col is not None
            return func.percentile_cont(0.5).within_group(
                sa.cast(selected_col, sa.Float)
            )
        if agg is TableAggregation.MODE:
            assert selected_col is not None
            return func.mode().within_group(selected_col)
        if agg is TableAggregation.N_UNIQUE:
            assert selected_col is not None
            return func.count(sa.distinct(selected_col))
        # VALUE_COUNTS
        return func.count(sa.column("id"))

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_DB_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.2, max=2),
        reraise=True,
    )
    async def _search_aggregation(
        self,
        *,
        table: Table,
        where_conditions: Sequence[ColumnElement[bool]],
        agg: TableAggregation | None,
        group_by: str | None,
        agg_field: str | None,
    ) -> TableAggregationRead | None:
        if agg is None:
            return None

        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        table_clause = sa.table(sanitized_table_name, schema=schema_name)
        field_lookup = self._build_field_lookup(table)

        aggregation_stmt = self._build_aggregation_stmt(
            table=table,
            table_clause=table_clause,
            where_conditions=where_conditions,
            agg=agg,
            agg_field=agg_field,
            group_by=group_by,
            field_lookup=field_lookup,
        )

        txn_cm = (
            self.session.begin_nested()
            if self.session.in_transaction()
            else self.session.begin()
        )
        async with txn_cm as txn:
            conn = await txn.session.connection()
            try:
                if group_by is None:
                    agg_result = await conn.execute(
                        aggregation_stmt,
                        execution_options={
                            "isolation_level": "READ COMMITTED",
                        },
                    )
                    agg_value = agg_result.scalar()
                    return TableAggregationRead(
                        agg=agg,
                        group_by=None,
                        agg_field=agg_field,
                        value=_normalize_aggregation_value(agg_value),
                        buckets=[],
                    )

                agg_result = await conn.execute(
                    aggregation_stmt,
                    execution_options={
                        "isolation_level": "READ COMMITTED",
                    },
                )
                buckets = [
                    TableAggregationBucket(
                        group=_normalize_aggregation_value(row.group_value),
                        value=_normalize_aggregation_value(row.agg_value),
                    )
                    for row in agg_result
                ]
                return TableAggregationRead(
                    agg=agg,
                    group_by=group_by,
                    agg_field=agg_field,
                    value=None,
                    buckets=buckets,
                )
            except _RETRYABLE_DB_EXCEPTIONS as e:
                self.logger.warning(
                    "Retryable DB exception occurred during search aggregation",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table.name,
                    schema=schema_name,
                )
                raise
            except ProgrammingError as e:
                while (cause := e.__cause__) is not None:
                    e = cause
                if isinstance(e, UndefinedTableError):
                    raise TracecatNotFoundError(
                        f"Table '{table.name}' does not exist"
                    ) from e
                self.logger.error(
                    "ProgrammingError during search aggregation",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table.name,
                    schema=schema_name,
                )
                raise ValueError(
                    "Invalid aggregation query for the given table schema"
                ) from e
            except DBAPIError as e:
                self.logger.error(
                    "Database error during search aggregation",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table.name,
                    schema=schema_name,
                )
                raise

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_DB_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.2, max=2),
        reraise=True,
    )
    async def exists_rows(
        self,
        table_name: str,
        *,
        columns: Sequence[str],
        values: Sequence[Any],
    ) -> bool:
        """Efficient existence check for rows matching column/value pairs.

        Uses a SQL EXISTS query so the database can short-circuit at the first match.
        """
        if len(values) != len(columns):
            raise ValueError("Values and column names must have the same length")

        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table_name)

        table_clause = sa.table(sanitized_table_name, schema=schema_name)
        cols = [sa.column(self._sanitize_identifier(c)) for c in columns]
        condition = sa.and_(
            *[col == value for col, value in zip(cols, values, strict=True)]
        )

        exists_stmt = sa.exists(sa.select(1).select_from(table_clause).where(condition))
        stmt = sa.select(exists_stmt)

        txn_cm = (
            self.session.begin_nested()
            if self.session.in_transaction()
            else self.session.begin()
        )
        async with txn_cm as txn:
            conn = await txn.session.connection()
            try:
                result = await conn.execute(
                    stmt,
                    execution_options={
                        "isolation_level": "READ COMMITTED",
                    },
                )
                exists_val = result.scalar()
                return bool(exists_val)
            except _RETRYABLE_DB_EXCEPTIONS as e:
                # Note: Context manager handles rollback (savepoint or full) automatically
                self.logger.warning(
                    "Retryable DB exception occurred during exists_rows",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=schema_name,
                )
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
                    "Unexpected DB exception occurred during exists_rows",
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
        cursor: str | None = None,
        reverse: bool = False,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
        group_by: str | None = None,
        agg: TableAggregation | None = None,
        agg_field: str | None = None,
    ) -> TableSearchResponse:
        """Search rows with cursor pagination and optional aggregation."""
        page_limit = (
            limit if limit is not None else config.TRACECAT__LIMIT_TABLE_SEARCH_DEFAULT
        )
        params = CursorPaginationParams(
            limit=page_limit,
            cursor=cursor,
            reverse=reverse,
        )

        where_conditions = self._build_search_where_conditions(
            table=table,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
        )
        rows = await self.list_rows(
            table=table,
            params=params,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
            order_by=order_by,
            sort=sort,
        )
        aggregation = await self._search_aggregation(
            table=table,
            where_conditions=where_conditions,
            agg=agg,
            group_by=group_by,
            agg_field=agg_field,
        )
        return TableSearchResponse(
            items=rows.items,
            next_cursor=rows.next_cursor,
            prev_cursor=rows.prev_cursor,
            has_more=rows.has_more,
            has_previous=rows.has_previous,
            total_estimate=rows.total_estimate,
            aggregation=aggregation,
        )

    async def list_rows(
        self,
        table: Table,
        params: CursorPaginationParams,
        search_term: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[dict[str, Any]]:
        """List rows in a table with cursor-based pagination.

        Args:
            table: The table to search in
            params: Cursor pagination parameters
            search_term: Text to search for across all text and JSONB columns
            start_time: Filter records created after this time
            end_time: Filter records created before this time
            updated_before: Filter records updated before this time
            updated_after: Filter records updated after this time
            order_by: Column name to order by (defaults to created_at)
            sort: Sort direction, "asc" or "desc" (defaults to desc)

        Returns:
            Cursor paginated response with matching rows

        Raises:
            TracecatNotFoundError: If the table does not exist
            ValueError: If search parameters are invalid or order_by column doesn't exist
        """
        schema_name = self._get_schema_name()
        sanitized_table_name = self._sanitize_identifier(table.name)
        conn = await self.session.connection()

        # Build the base query
        stmt = sa.select(sa.text("*")).select_from(
            sa.table(sanitized_table_name, schema=schema_name)
        )

        where_conditions = self._build_search_where_conditions(
            table=table,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
        )

        # Apply WHERE conditions if any
        if where_conditions:
            stmt = stmt.where(sa.and_(*where_conditions))

        # Determine sort column and direction
        sort_column = order_by or "created_at"
        sort_direction = sort or "desc"

        # Validate the sort column exists in the table
        valid_columns = {col.name for col in table.columns}
        valid_columns.update(["id", "created_at", "updated_at"])  # Always available
        if sort_column not in valid_columns:
            raise ValueError(f"Invalid order_by column: {sort_column}")

        sort_col = sa.column(self._sanitize_identifier(sort_column))

        # Apply cursor-based pagination with sort-column-aware filtering
        if params.cursor:
            try:
                cursor_data = BaseCursorPaginator.decode_cursor(params.cursor)
            except Exception as e:
                raise ValueError(f"Invalid cursor: {e}") from e

            cursor_id = UUID(cursor_data.id)

            # Check if cursor was created with the same sort column
            cursor_sort_value = cursor_data.sort_value
            cursor_has_sort_value = (
                cursor_data.sort_column == sort_column and cursor_sort_value is not None
            )

            if cursor_has_sort_value:
                # Use sort column value for cursor filtering
                sort_cursor_value = cursor_sort_value

                # Composite filtering: (sort_col, id) matches ORDER BY
                if sort_direction == "asc":
                    if params.reverse:
                        # Going backward: get records before cursor in sort order
                        stmt = stmt.where(
                            sa.or_(
                                sort_col < sort_cursor_value,
                                sa.and_(
                                    sort_col == sort_cursor_value,
                                    sa.column("id") < cursor_id,
                                ),
                            )
                        )
                    else:
                        # Going forward: get records after cursor in sort order
                        stmt = stmt.where(
                            sa.or_(
                                sort_col > sort_cursor_value,
                                sa.and_(
                                    sort_col == sort_cursor_value,
                                    sa.column("id") > cursor_id,
                                ),
                            )
                        )
                else:
                    # Descending order
                    if params.reverse:
                        # Going backward: get records after cursor in sort order
                        stmt = stmt.where(
                            sa.or_(
                                sort_col > sort_cursor_value,
                                sa.and_(
                                    sort_col == sort_cursor_value,
                                    sa.column("id") > cursor_id,
                                ),
                            )
                        )
                    else:
                        # Going forward: get records before cursor in sort order
                        stmt = stmt.where(
                            sa.or_(
                                sort_col < sort_cursor_value,
                                sa.and_(
                                    sort_col == sort_cursor_value,
                                    sa.column("id") < cursor_id,
                                ),
                            )
                        )

        # Apply sorting: (sort_col, id) for stable pagination
        # Use id as tie-breaker unless we're already sorting by id
        if sort_column == "id":
            # No tie-breaker needed when sorting by id (already unique)
            if sort_direction == "asc":
                stmt = stmt.order_by(sort_col.asc())
            else:
                stmt = stmt.order_by(sort_col.desc())
        else:
            # Add id as tie-breaker for non-unique columns
            if sort_direction == "asc":
                stmt = stmt.order_by(sort_col.asc(), sa.column("id").asc())
            else:
                stmt = stmt.order_by(sort_col.desc(), sa.column("id").desc())

        # Fetch limit + 1 to determine if there are more items
        stmt = stmt.limit(params.limit + 1)

        try:
            result = await conn.execute(stmt)
            rows = [dict(row) for row in result.mappings().all()]
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
                "Unexpected DB exception occurred during paginated search",
                kind=type(e).__name__,
                error=str(e),
                table=table.name,
                schema=schema_name,
            )
            raise

        # Check if there are more items
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]
        has_previous = params.cursor is not None

        # Generate cursors with sort column info for proper pagination
        next_cursor = None
        prev_cursor = None

        if rows:
            if has_more:
                # Generate next cursor from the last item
                last_item = rows[-1]
                next_cursor = BaseCursorPaginator.encode_cursor(
                    last_item["id"],
                    sort_column=sort_column,
                    sort_value=last_item.get(sort_column),
                )

            if params.cursor:
                # If we used a cursor to get here, we can go back
                first_item = rows[0]
                prev_cursor = BaseCursorPaginator.encode_cursor(
                    first_item["id"],
                    sort_column=sort_column,
                    sort_value=first_item.get(sort_column),
                )

        # If we were doing reverse pagination, swap the cursors and reverse items
        if params.reverse:
            rows = list(reversed(rows))
            next_cursor, prev_cursor = prev_cursor, next_cursor
            has_more, has_previous = has_previous, has_more

        return CursorPaginatedResponse(
            items=rows,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
        )

    async def batch_insert_rows(
        self,
        table: Table,
        rows: list[dict[str, Any]],
        *,
        upsert: bool = False,
        chunk_size: int = 1000,
    ) -> int:
        """Insert multiple rows into the table atomically.

        Args:
            table: The table to insert into
            rows: List of row data to insert
            upsert: If True, update existing rows on conflict based on unique index.
                   Uses COALESCE to preserve existing column values when the new
                   value is NULL (i.e., when a row doesn't include that column)
            chunk_size: Maximum number of rows to insert in a single transaction

        Returns:
            Number of rows affected (inserted + updated)

        Raises:
            ValueError: If the batch size exceeds the chunk_size, table lacks
                       unique index for upsert, or rows lack index columns
            DBAPIError: If there's a database error during insertion
        """
        if not rows:
            return 0

        if len(rows) > chunk_size:
            raise ValueError(f"Batch size {len(rows)} exceeds maximum of {chunk_size}")

        schema_name = self._get_schema_name()

        sanitized_table_name = self._sanitize_identifier(table.name)

        # Group rows by their column sets to avoid inserting NULL into missing columns.
        rows_by_columns: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            normalised_row = self._normalize_row_inputs(table, row)
            rows_by_columns[frozenset(normalised_row.keys())].append(normalised_row)

        column_type_map = {
            column.name: self._sa_type_for_column(SqlType(column.type))
            for column in table.columns
        }

        conn = await self.session.connection()

        total_affected = 0

        # If we need upsert behaviour, fetch the unique index once.
        index: list[str] | None = None
        if upsert:
            index = await self.get_index(table)

            if not index:
                raise ValueError("Table must have at least one unique index for upsert")
            if len(index) > 1:
                raise ValueError(
                    "Table cannot have multiple unique indexes. This is an unexpected error. Please contact support."
                )

        # Iterate over groups and execute separate INSERT/UPSERT statements.
        for col_set, group_rows in rows_by_columns.items():
            # Sanitize column identifiers for this group
            cols = [
                sa.column(
                    self._sanitize_identifier(col),
                    type_=column_type_map.get(col),
                )
                for col in col_set
            ]
            table_obj = sa.table(sanitized_table_name, *cols, schema=schema_name)

            if not upsert:
                stmt = sa.insert(table_obj).values(group_rows)
            else:
                # Ensure each row contains the unique index column(s)
                assert index is not None  # mypy / type checker hint
                for row in group_rows:
                    if not all(k in row for k in index):
                        raise ValueError(
                            "Each row to upsert must contain the unique index column"
                        )

                pg_stmt = insert(table_obj).values(group_rows)

                # Build a mapping of *sanitized* Column objects so we can use them safely
                col_objs = {  # key is the sanitized column name
                    col_obj.key: col_obj for col_obj in cols
                }

                # Columns to update on conflict: all non-index columns present in this group.
                #
                # We wrap the new value in COALESCE(new, existing) so that if the incoming
                # value is NULL we keep the existing value. This matches the behaviour
                # promised in the function docstring.
                update_dict = {}
                for raw_col_name in col_set:
                    sanitized_name = self._sanitize_identifier(raw_col_name)
                    if sanitized_name in index:
                        # Never update columns that are part of the unique index
                        continue

                    column_obj = col_objs[sanitized_name]
                    update_dict[column_obj] = sa.func.coalesce(
                        pg_stmt.excluded[sanitized_name],
                        column_obj,
                    )

                if update_dict:
                    stmt = pg_stmt.on_conflict_do_update(
                        index_elements=index,
                        set_=update_dict,
                    )
                else:
                    # Nothing to update (e.g., the only columns present are the unique index)
                    stmt = pg_stmt.on_conflict_do_nothing(index_elements=index)

            try:
                result = await conn.execute(stmt)
                total_affected += result.rowcount
            except Exception as e:
                # Re-raise as DBAPIError for consistency
                raise DBAPIError("Failed to insert batch", str(e), e) from e

        # Flush once at the end to ensure changes are persisted within the transaction.
        await self.session.flush()
        return total_affected


class TablesService(BaseTablesService):
    """Transactional tables service."""

    @require_scope("table:create")
    async def create_table(self, params: TableCreate) -> Table:
        result = await super().create_table(params)
        await self.session.commit()
        await self.session.refresh(result)
        return result

    @require_scope("table:create")
    async def import_table_from_csv(
        self,
        *,
        contents: bytes,
        filename: str | None = None,
        table_name: str | None = None,
        chunk_size: int = 1000,
    ) -> tuple[Table, int, list[InferredCSVColumn]]:
        """Create a new table by inferring schema and rows from a CSV file."""
        try:
            csv_text = contents.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise TracecatImportError(
                "CSV import requires UTF-8 encoded files"
            ) from exc

        first_pass = StringIO(csv_text)
        reader = csv.DictReader(first_pass)
        headers = reader.fieldnames

        inferer = CSVSchemaInferer.initialise(headers or [])
        for row in reader:
            inferer.observe(row)
        first_pass.close()

        inferred_columns = inferer.result()

        if not inferred_columns:
            raise TracecatImportError("CSV file does not contain any columns")

        raw_table_name = table_name
        if not raw_table_name and filename:
            raw_table_name = Path(filename).stem
        base_table_name = generate_table_name(raw_table_name)
        unique_table_name = await self._find_unique_table_name(base_table_name)

        column_defs = [
            TableColumnCreate(name=column.name, type=column.type)
            for column in inferred_columns
        ]
        table = await self.create_table(
            TableCreate(name=unique_table_name, columns=column_defs)
        )

        second_pass = StringIO(csv_text)
        reader = csv.DictReader(second_pass)

        chunk: list[dict[str, Any]] = []
        rows_inserted = 0
        try:
            for row in reader:
                mapped_row: dict[str, Any] = {}
                for column in inferred_columns:
                    raw_value = row.get(column.original_name)
                    if raw_value is None:
                        mapped_row[column.name] = None
                        continue
                    if isinstance(raw_value, str) and raw_value.strip() == "":
                        if column.type is SqlType.TEXT:
                            mapped_row[column.name] = ""
                        else:
                            mapped_row[column.name] = None
                        continue
                    value_to_convert = raw_value
                    if isinstance(raw_value, str) and column.type is not SqlType.TEXT:
                        value_to_convert = raw_value.strip()
                    try:
                        mapped_row[column.name] = convert_value(
                            value_to_convert, column.type
                        )
                    except TypeError as exc:
                        raise TracecatImportError(
                            f"Cannot convert value {raw_value!r} in column "
                            f"{column.original_name!r} to type {column.type}"
                        ) from exc
                if mapped_row:
                    chunk.append(mapped_row)
                if len(chunk) >= chunk_size:
                    rows_inserted += await self._insert_import_chunk(
                        table, chunk, chunk_size=chunk_size
                    )
                    chunk = []

            if chunk:
                rows_inserted += await self._insert_import_chunk(
                    table, chunk, chunk_size=chunk_size
                )
        except Exception:
            await self._cleanup_failed_import(table)
            raise
        finally:
            second_pass.close()

        await self.session.refresh(table)
        return table, rows_inserted, inferred_columns

    async def _insert_import_chunk(
        self, table: Table, chunk: list[dict[str, Any]], *, chunk_size: int
    ) -> int:
        if not chunk:
            return 0
        try:
            return await self.batch_insert_rows(table, chunk, chunk_size=chunk_size)
        except DBAPIError as exc:
            # Get error message, removing SQL queries that may contain sensitive data
            cause = exc.__cause__ or exc
            message = str(cause).strip()
            if "[SQL:" in message:
                message = message.split("[SQL:", 1)[0].strip()
            if not message:
                message = cause.__class__.__name__
            raise TracecatImportError(
                f"Failed to insert rows into table '{table.name}': {message}"
            ) from exc

    async def _cleanup_failed_import(self, table: Table) -> None:
        try:
            # Use base implementation for internal rollback cleanup so import
            # failure handling does not depend on external delete scope grants.
            await super().delete_table(table)
            await self.session.commit()
        except Exception as cleanup_error:
            logger.error(
                "Failed to clean up table after import failure",
                table_id=str(table.id),
                error=cleanup_error,
            )

    @require_scope("table:update")
    async def update_table(self, table: Table, params: TableUpdate) -> Table:
        result = await super().update_table(table, params)
        await self.session.commit()
        await self.session.refresh(result)
        return result

    @require_scope("table:delete")
    async def delete_table(self, table: Table) -> None:
        await super().delete_table(table)
        await self.session.commit()

    @require_scope("table:create")
    async def insert_row(self, table: Table, params: TableRowInsert) -> dict[str, Any]:
        result = await super().insert_row(table, params)
        await self.session.commit()
        return result

    @require_scope("table:update")
    async def update_row(
        self, table: Table, row_id: UUID, data: dict[str, Any]
    ) -> dict[str, Any]:
        result = await super().update_row(table, row_id, data)
        await self.session.commit()
        return result

    @require_scope("table:delete")
    async def delete_row(self, table: Table, row_id: UUID) -> None:
        await super().delete_row(table, row_id)
        await self.session.commit()

    @require_scope("table:delete")
    async def batch_delete_rows(self, table: Table, row_ids: list[UUID]) -> int:
        result = await super().batch_delete_rows(table, row_ids)
        await self.session.commit()
        return result

    @require_scope("table:update")
    async def batch_update_rows(
        self, table: Table, row_ids: list[UUID], data: dict[str, Any]
    ) -> int:
        result = await super().batch_update_rows(table, row_ids, data)
        await self.session.commit()
        return result

    @require_scope("table:create")
    async def create_column(
        self, table: Table, params: TableColumnCreate
    ) -> TableColumn:
        column = await super().create_column(table, params)
        await self.session.commit()
        await self.session.refresh(column)
        await self.session.refresh(table)
        return column

    @require_scope("table:update")
    async def update_column(
        self, column: TableColumn, params: TableColumnUpdate
    ) -> TableColumn:
        column = await super().update_column(column, params)
        await self.session.commit()
        await self.session.refresh(column)
        return column

    @require_scope("table:delete")
    async def delete_column(self, column: TableColumn) -> None:
        await super().delete_column(column)
        await self.session.commit()

    @require_scope("table:create")
    async def batch_insert_rows(
        self,
        table: Table,
        rows: list[dict[str, Any]],
        *,
        upsert: bool = False,
        chunk_size: int = 1000,
    ) -> int:
        result = await super().batch_insert_rows(
            table, rows, upsert=upsert, chunk_size=chunk_size
        )
        await self.session.commit()
        return result


class TableEditorService(BaseWorkspaceService):
    """Service for editing workspace-scoped tables.

    This is a utility service for DDL operations (add/update/delete columns, rows)
    on tables within a workspace schema.

    The role represents the operator (the user/service performing the action).
    """

    service_name = "table_editor"

    def __init__(
        self,
        session: AsyncSession,
        role: Role | None = None,
        *,
        table_name: str,
        schema_name: str,
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
        # Map SELECT -> TEXT, MULTI_SELECT -> JSONB for physical storage
        if params.type is SqlType.SELECT:
            column_type_sql = SqlType.TEXT.value
        elif params.type is SqlType.MULTI_SELECT:
            column_type_sql = SqlType.JSONB.value
        else:
            column_type_sql = params.type.value
        column_def = [f"{params.name} {column_type_sql}"]
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
            new_type = SqlType(set_fields["type"])
            # Map SELECT -> TEXT, MULTI_SELECT -> JSONB for physical storage
            if new_type is SqlType.SELECT:
                column_type_sql = SqlType.TEXT.value
            elif new_type is SqlType.MULTI_SELECT:
                column_type_sql = SqlType.JSONB.value
            else:
                column_type_sql = (
                    "BIGINT" if SqlType(new_type) == SqlType.INTEGER else new_type
                )
            await conn.execute(
                sa.DDL(
                    "ALTER TABLE %s ALTER COLUMN %s TYPE %s",
                    (full_table_name, new_name, column_type_sql),
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
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        reverse: bool = False,
    ) -> CursorPaginatedResponse[dict[str, Any]]:
        """List rows with cursor-based pagination ordered by row ID."""
        conn = await self.session.connection()
        stmt = sa.select("*").select_from(
            sa.table(self.table_name, schema=self.schema_name)
        )

        if cursor:
            cursor_data = BaseCursorPaginator.decode_cursor(cursor)
            cursor_id = UUID(cursor_data.id)
            if reverse:
                stmt = stmt.where(sa.column("id") < cursor_id)
            else:
                stmt = stmt.where(sa.column("id") > cursor_id)

        if reverse:
            stmt = stmt.order_by(sa.column("id").desc())
        else:
            stmt = stmt.order_by(sa.column("id").asc())

        stmt = stmt.limit(limit + 1)
        result = await conn.execute(stmt)
        rows = [dict(row) for row in result.mappings().all()]

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        has_previous = cursor is not None

        next_cursor: str | None = None
        prev_cursor: str | None = None
        if rows:
            if has_more:
                next_cursor = BaseCursorPaginator.encode_cursor(rows[-1]["id"])
            if cursor:
                prev_cursor = BaseCursorPaginator.encode_cursor(rows[0]["id"])

        if reverse:
            rows = list(reversed(rows))
            next_cursor, prev_cursor = prev_cursor, next_cursor
            has_more, has_previous = has_previous, has_more

        return CursorPaginatedResponse(
            items=rows,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
        )

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
            column_info = col_map.get(col)
            if column_info is None:
                raise ValueError(
                    f"Column '{col}' does not exist in table {self.table_name}"
                )
            column_type = column_info["type"]
            if value is None:
                coerced_value = None
            elif isinstance(column_type, sa.Date):
                coerced_value = coerce_to_date(value)
            elif getattr(column_type, "timezone", False):
                coerced_value = coerce_to_utc_datetime(value)
            else:
                coerced_value = value
            value_clauses[col] = sa.bindparam(col, coerced_value, type_=column_type)
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
        col_map = {c["name"]: c for c in await self.get_columns()}

        # Build update statement using SQLAlchemy
        value_clauses: dict[str, sa.BindParameter] = {}
        cols = []
        for column_name, value in data.items():
            column_info = col_map.get(column_name)
            if column_info is None:
                raise ValueError(
                    f"Column '{column_name}' does not exist in table {self.table_name}"
                )
            column_type = column_info["type"]
            if value is None:
                coerced_value = None
            elif isinstance(column_type, sa.Date):
                coerced_value = coerce_to_date(value)
            elif getattr(column_type, "timezone", False):
                coerced_value = coerce_to_utc_datetime(value)
            else:
                coerced_value = value
            cols.append(sa.column(sanitize_identifier(column_name)))
            value_clauses[column_name] = sa.bindparam(
                column_name, coerced_value, type_=column_type
            )

        stmt = (
            sa.update(sa.table(self.table_name, *cols, schema=self.schema_name))
            .where(sa.column("id") == row_id)
            .values(**value_clauses)
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
