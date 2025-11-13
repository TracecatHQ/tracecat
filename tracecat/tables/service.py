import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import csv
from collections import defaultdict
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from asyncpg.exceptions import (
    InFailedSQLTransactionError,
    InvalidCachedStatementError,
    UndefinedTableError,
)
from sqlalchemy.dialects.postgresql import JSONB, array, insert
from sqlalchemy.exc import DBAPIError, IntegrityError, NoResultFound, ProgrammingError
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.schema import TableClause
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.controls import require_access_level
from tracecat.db.models import CaseTableRow, Table, TableColumn
from tracecat.exceptions import (
    TracecatAuthorizationError,
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
from tracecat.service import BaseService
from tracecat.tables.common import (
    coerce_to_utc_datetime,
    convert_value,
    handle_default_value,
    is_valid_sql_type,
    parse_postgres_default,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import (
    CSVSchemaInferer,
    InferredCSVColumn,
    generate_table_name,
)
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)

_RETRYABLE_DB_EXCEPTIONS = (
    InvalidCachedStatementError,
    InFailedSQLTransactionError,
)


@dataclass(slots=True)
class _TableContext:
    """Runtime context for dynamic workspace tables."""

    schema: str
    table_name: str
    table: TableClause
    data_column: ColumnElement[Any]


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

    def _workspace_id(self) -> WorkspaceUUID:
        """Get the workspace ID for the current role."""
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise TracecatAuthorizationError("Workspace ID is required")
        return WorkspaceUUID.new(workspace_id)

    def _table_context(self, table: Table) -> _TableContext:
        schema_name = self._get_schema_name()
        sanitized_table = self._sanitize_identifier(table.name)
        data_col = sa.column("data", type_=JSONB)
        table_clause = sa.table(
            sanitized_table,
            sa.column("id"),
            sa.column("created_at"),
            sa.column("updated_at"),
            data_col,
            schema=schema_name,
        )
        return _TableContext(
            schema=schema_name,
            table_name=sanitized_table,
            table=table_clause,
            data_column=data_col,
        )

    def _normalize_row_inputs(
        self,
        table: Table,
        data: dict[str, Any],
        *,
        include_defaults: bool = False,
    ) -> dict[str, Any]:
        """Coerce row inputs to the expected SQL types."""
        if not data and not include_defaults:
            return {}

        column_index = self._column_index(table)
        normalised: dict[str, Any] = {}

        for column_name, raw_value in data.items():
            column = column_index.get(column_name)
            if column is None:
                raise ValueError(
                    f"Column '{column_name}' does not exist in table '{table.name}'"
                )
            normalised[column_name] = self._normalise_value(column, raw_value)

        if not include_defaults:
            return normalised

        for column in table.columns:
            if column.name in normalised:
                continue

            sql_type = SqlType(column.type)
            if column.default is not None:
                normalised[column.name] = self._default_json_value(
                    sql_type, column.default
                )
            elif column.nullable:
                normalised[column.name] = None
            else:
                raise ValueError(
                    f"Column '{column.name}' is required but no value was provided"
                )

        return normalised

    def _normalise_value(self, column: TableColumn, value: Any) -> Any:
        if value is None:
            return None

        sql_type = SqlType(column.type)

        # Auto-promote INTEGER to BIGINT if value exceeds INT32 range
        if sql_type is SqlType.INTEGER and isinstance(value, int):
            if value < -(2**31) or value > (2**31 - 1):
                column.type = SqlType.BIGINT.value
                self.session.add(column)  # Mark for update
                sql_type = SqlType.BIGINT

        if sql_type is SqlType.ENUM:
            candidate = str(value).strip()
            allowed = self._enum_values(column)
            if allowed and candidate not in allowed:
                raise ValueError(
                    f"Invalid value '{candidate}' for column '{column.name}'. "
                    f"Allowed values are: {', '.join(allowed)}"
                )
            return candidate

        if sql_type in {SqlType.TIMESTAMP, SqlType.TIMESTAMPTZ}:
            return coerce_to_utc_datetime(value).isoformat()

        return value

    @staticmethod
    def _column_index(table: Table) -> dict[str, TableColumn]:
        return {column.name: column for column in table.columns}

    def _jsonb_text_path(
        self, context: _TableContext, column_name: str
    ) -> ColumnElement[Any]:
        """Return an expression that extracts a JSONB column as text."""
        sanitized = self._sanitize_identifier(column_name)
        return sa.func.jsonb_extract_path_text(
            context.data_column, sa.literal(sanitized)
        )

    def _row_filter_conditions(
        self,
        table: Table,
        context: _TableContext,
        *,
        search_term: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
        updated_before: datetime | None,
        updated_after: datetime | None,
    ) -> list[ColumnElement[Any]]:
        conditions: list[ColumnElement[Any]] = []

        if search_term:
            if len(search_term) > 1000:
                raise ValueError("Search term cannot exceed 1000 characters")
            if "\x00" in search_term:
                raise ValueError("Search term cannot contain null bytes")

            searchable_columns = [
                column
                for column in table.columns
                if column.type in {SqlType.TEXT.value, SqlType.JSONB.value}
            ]
            if searchable_columns:
                search_pattern = sa.func.concat("%", search_term, "%")
                search_conditions = [
                    self._jsonb_text_path(context, column.name).ilike(search_pattern)
                    for column in searchable_columns
                ]
                conditions.append(sa.or_(*search_conditions))
            else:
                self.logger.warning(
                    "No searchable columns found for text search",
                    table=table.name,
                    search_term=search_term,
                )

        if start_time is not None:
            conditions.append(sa.column("created_at") >= start_time)
        if end_time is not None:
            conditions.append(sa.column("created_at") <= end_time)
        if updated_after is not None:
            conditions.append(sa.column("updated_at") >= updated_after)
        if updated_before is not None:
            conditions.append(sa.column("updated_at") <= updated_before)

        return conditions

    def _row_select(
        self,
        table: Table,
        *,
        search_term: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
    ) -> tuple[Select, _TableContext]:
        context = self._table_context(table)
        stmt = sa.select(sa.text("*")).select_from(context.table)
        where_conditions = self._row_filter_conditions(
            table,
            context,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
        )
        if where_conditions:
            stmt = stmt.where(sa.and_(*where_conditions))
        return stmt, context

    def _flatten_record(self, row: Mapping[str, Any]) -> dict[str, Any]:
        materialised = dict(row)
        payload = materialised.pop("data", None)
        if isinstance(payload, Mapping):
            materialised.update(payload)
        return materialised

    def _enum_metadata(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("Enum columns expect an object with an 'enum_values' list")

        raw_values = payload.get("enum_values")
        if raw_values is None:
            raise ValueError("Enum columns require an 'enum_values' list")
        if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, Sequence):
            raise ValueError("Enum 'enum_values' must be a list of strings")

        cleaned: list[str] = []
        for raw in raw_values:
            if not isinstance(raw, str):
                raise ValueError("Enum values must be strings")
            candidate = raw.strip()
            if not candidate:
                raise ValueError("Enum values cannot be empty")
            if candidate not in cleaned:
                cleaned.append(candidate)

        if not cleaned:
            raise ValueError("Enum columns require at least one value")

        default_value = payload.get("default") or payload.get("value")
        if default_value is not None:
            if not isinstance(default_value, str):
                raise ValueError("Enum default must be a string")
            candidate = default_value.strip()
            if candidate and candidate not in cleaned:
                raise ValueError(
                    f"Enum default '{candidate}' must be one of: {', '.join(cleaned)}"
                )
            default_value = candidate or None

        metadata: dict[str, Any] = {"enum_values": cleaned}
        if default_value is not None:
            metadata["default"] = default_value
            metadata["value"] = default_value
        return metadata

    def _enum_values(self, column: TableColumn) -> tuple[str, ...]:
        raw = column.default
        if not isinstance(raw, Mapping):
            return ()
        try:
            metadata = self._enum_metadata(raw)
        except ValueError as exc:
            self.logger.warning(
                "Invalid enum metadata encountered",
                column=column.name,
                table=column.table.name if column.table else None,
                error=str(exc),
            )
            return ()
        return tuple(metadata["enum_values"])

    def _column_metadata(self, sql_type: SqlType, default: Any | None) -> Any | None:
        if default is None:
            return None
        if sql_type is SqlType.ENUM:
            return self._enum_metadata(default)
        return handle_default_value(sql_type, default)

    def _default_json_value(
        self, sql_type: SqlType, default_payload: Any | None
    ) -> Any | None:
        if default_payload is None:
            return None

        if sql_type is SqlType.ENUM:
            if isinstance(default_payload, Mapping):
                value = default_payload.get("default") or default_payload.get("value")
                return value if value is not None else None
            return str(default_payload)

        if isinstance(default_payload, str):
            parsed = parse_postgres_default(default_payload)
        else:
            parsed = default_payload

        if parsed in (None, ""):
            return None
        if isinstance(parsed, str) and parsed.lower() == "null":
            return None

        try:
            if sql_type is SqlType.BOOLEAN:
                if isinstance(parsed, str):
                    lowered = parsed.lower()
                    if lowered in {"true", "1"}:
                        return True
                    if lowered in {"false", "0"}:
                        return False
                return bool(parsed)
            if sql_type is SqlType.INTEGER:
                return int(parsed)
            if sql_type is SqlType.NUMERIC:
                return float(parsed)
            if sql_type in {SqlType.TIMESTAMP, SqlType.TIMESTAMPTZ}:
                try:
                    return coerce_to_utc_datetime(parsed).isoformat()
                except Exception:
                    if isinstance(parsed, str):
                        return parsed
                    return None
            if sql_type is SqlType.JSONB and isinstance(parsed, str):
                try:
                    return json.loads(parsed)
                except json.JSONDecodeError:
                    return parsed
        except Exception:
            return None

        return parsed

    async def _reset_column_values(
        self,
        table: Table,
        column_name: str,
        default_payload: Any | None,
        sql_type: SqlType,
    ) -> None:
        context = self._table_context(table)
        sanitized_column = self._sanitize_identifier(column_name)
        conn = await self.session.connection()

        default_value = self._default_json_value(sql_type, default_payload)
        path = sa.literal_column(f"ARRAY['{sanitized_column}']")

        if default_value is None:
            new_value_expr = sa.literal_column("'null'::jsonb")
            params: dict[str, Any] = {}
        else:
            new_value_expr = sa.bindparam("new_value", type_=JSONB)
            params = {"new_value": default_value}

        stmt = (
            sa.update(context.table)
            .values(
                data=sa.func.jsonb_set(
                    context.data_column,
                    path,
                    new_value_expr,
                    True,
                )
            )
            .where(context.data_column.has_key(sanitized_column))
        )
        await conn.execute(stmt, params)

    async def _ensure_no_null_values(self, table: Table, column_name: str) -> None:
        context = self._table_context(table)
        sanitized_column = self._sanitize_identifier(column_name)
        value_expr = self._jsonb_text_path(context, column_name)

        stmt = (
            sa.select(sa.literal(True))
            .select_from(context.table)
            .where(
                sa.or_(
                    sa.not_(context.data_column.has_key(sanitized_column)),
                    value_expr.is_(None),
                )
            )
            .limit(1)
        )

        conn = await self.session.connection()
        result = await conn.execute(stmt)
        if result.scalar():
            raise ValueError(
                f"Cannot set column '{column_name}' to disallow nulls while existing rows contain null or missing values."
            )

    async def _ensure_unique_values(self, table: Table, column_name: str) -> None:
        context = self._table_context(table)
        value_expr = self._jsonb_text_path(context, column_name)

        stmt = (
            sa.select(value_expr.label("value"), sa.func.count().label("count"))
            .select_from(context.table)
            .where(value_expr.isnot(None))
            .group_by(value_expr)
            .having(sa.func.count() > 1)
            .limit(1)
        )

        conn = await self.session.connection()
        result = await conn.execute(stmt)
        duplicate = result.mappings().first()
        if duplicate is not None:
            raise ValueError(
                f"Cannot create a unique index on '{column_name}' because duplicate value '{duplicate['value']}' exists."
            )

    async def _rename_jsonb_key(self, table: Table, old_key: str, new_key: str) -> None:
        if old_key == new_key:
            return

        sanitized_old = self._sanitize_identifier(old_key)
        sanitized_new = self._sanitize_identifier(new_key)

        context = self._table_context(table)
        conn = await self.session.connection()

        old_key_literal = sa.literal(sanitized_old)
        path_literal = array([sa.literal(sanitized_new)])
        new_value = sa.func.coalesce(
            sa.func.jsonb_extract_path(context.data_column, old_key_literal),
            sa.literal_column("'null'::jsonb"),
        )
        updated_data = sa.func.jsonb_set(
            context.data_column, path_literal, new_value, sa.true()
        ).op("-")(old_key_literal)

        stmt = (
            sa.update(context.table)
            .values(data=updated_data)
            .where(context.data_column.has_key(sanitized_old))
        )
        await conn.execute(stmt)

    async def _drop_unique_index(self, table: Table, column_name: str) -> None:
        schema_name = self._get_schema_name()
        sanitized_column = self._sanitize_identifier(column_name)
        sanitized_table_name = self._sanitize_identifier(table.name)
        index_name = f"uq_{sanitized_table_name}_{sanitized_column}"

        conn = await self.session.connection()
        ddl = sa.text(f'DROP INDEX IF EXISTS "{schema_name}"."{index_name}"')
        await conn.execute(ddl)

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
        index_names: list[str] = []
        for index in indexes:
            if not index.get("unique"):
                continue

            column_names = index.get("column_names") or []
            if (
                len(column_names) == 1
                and isinstance(column_names[0], str)
                and column_names[0] not in index_names
            ):
                index_names.append(column_names[0])
                continue

            for expression in index.get("expressions") or []:
                if not isinstance(expression, str):
                    continue
                match = re.search(r"data\s*->>\s*'([^']+)'", expression)
                if match:
                    column = match.group(1)
                    if column not in index_names:
                        index_names.append(column)

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
            sa.Column(
                "data",
                JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
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

        # Create columns if specified
        # Call base class method directly to avoid per-column commits
        for col_params in params.columns:
            await BaseTablesService.create_column(self, table, col_params)

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

        # Validate SQL type first
        if not is_valid_sql_type(params.type):
            raise ValueError(f"Invalid type: SqlType.{params.type.name}")
        sql_type = SqlType(params.type)

        # Handle default value / metadata based on type
        column_metadata = self._column_metadata(sql_type, params.default)

        # Create the column metadata
        column = TableColumn(
            table_id=table.id,
            name=column_name,
            type=sql_type.value,
            nullable=params.nullable,
            default=column_metadata,  # Persist enum metadata in column definition
        )
        self.session.add(column)
        await self.session.flush()
        return column

    @require_access_level(AccessLevel.ADMIN)
    async def update_column(
        self,
        column: TableColumn,
        params: TableColumnUpdate,
    ) -> TableColumn:
        """Update column metadata.

        Args:
            column: The column metadata to update
            params: Parameters for updating the column

        Returns:
            The updated TableColumn metadata object

        Raises:
            ValueError: If the column type is invalid
        """
        set_fields = params.model_dump(exclude_unset=True)
        is_index = set_fields.pop("is_index", None)

        # Fetch table explicitly to avoid lazy loading issues
        table = await self.get_table(column.table_id)
        existing_index_columns = await self.get_index(table)
        had_unique_index = column.name in existing_index_columns

        current_type = SqlType(column.type)
        next_nullable = set_fields.get("nullable", column.nullable)
        # Validate that no null values exist if we're making the column non-nullable
        if next_nullable is False and "nullable" in set_fields:
            await self._ensure_no_null_values(table, column.name)

        requested_name = set_fields.get("name")
        new_name = (
            self._sanitize_identifier(requested_name)
            if isinstance(requested_name, str)
            else column.name
        )
        rename_requested = new_name != column.name

        requested_type = set_fields.get("type")
        if requested_type is not None and not is_valid_sql_type(requested_type):
            raise ValueError(f"Invalid type: {requested_type}")
        new_type = (
            SqlType(requested_type)
            if requested_type is not None
            else SqlType(column.type)
        )
        type_changed = new_type != current_type

        if "default" in set_fields:
            set_fields["default"] = self._column_metadata(
                new_type, set_fields["default"]
            )

        index_dropped = False
        if rename_requested:
            # Check for duplicate names by querying directly
            existing_stmt = select(TableColumn.name).where(
                TableColumn.table_id == column.table_id,
                TableColumn.id != column.id,
            )
            result = await self.session.exec(existing_stmt)
            existing_names = set(result.all())
            if new_name in existing_names:
                raise ValueError(f"Column '{new_name}' already exists")
            if had_unique_index:
                await self._drop_unique_index(table, column.name)
                index_dropped = True
            await self._rename_jsonb_key(table, column.name, new_name)

        if new_type is SqlType.ENUM:
            metadata_payload = (
                set_fields.get("default") if "default" in set_fields else column.default
            )
            if metadata_payload is None:
                raise ValueError("Enum columns require an 'enum_values' definition")
            enum_metadata = self._enum_metadata(metadata_payload)
            if "default" not in set_fields:
                set_fields["default"] = enum_metadata

        for key, raw_value in set_fields.items():
            if key not in ("name", "type", "nullable", "default"):
                continue

            if key == "name" and isinstance(raw_value, str):
                value = new_name
            elif key == "type":
                value = new_type.value
            else:
                value = raw_value

            setattr(column, key, value)

        if rename_requested:
            column.name = new_name

        if requested_type is not None:
            column.type = new_type.value
            if "default" not in set_fields:
                column.default = None
            if had_unique_index and not index_dropped:
                await self._drop_unique_index(table, column.name)
                index_dropped = True

        if is_index is False and had_unique_index and not index_dropped:
            await self._drop_unique_index(table, column.name)
            index_dropped = True

        await self.session.flush()

        reset_required = type_changed or (
            new_type is SqlType.ENUM
            and (requested_type is not None or "default" in set_fields)
        )
        if reset_required:
            default_payload = set_fields.get("default", column.default)
            if next_nullable is False:
                resolved_default = self._default_json_value(new_type, default_payload)
                if resolved_default is None:
                    raise ValueError(
                        f"Column '{new_name}' requires a default value when changing type because it is non-nullable."
                    )
            await self._reset_column_values(table, new_name, default_payload, new_type)

        should_have_unique_index = had_unique_index if is_index is None else is_index
        if should_have_unique_index:
            current_index_columns = await self.get_index(table)
            if new_name not in current_index_columns:
                await self.create_unique_index(table, new_name)

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
        sanitized_table_name = self._sanitize_identifier(table.name)

        # Sanitize column names to prevent SQL injection
        sanitized_column = self._sanitize_identifier(column_name)

        if sanitized_column not in {col.name for col in table.columns}:
            raise ValueError(
                f"Column '{column_name}' does not exist on table '{table.name}'"
            )

        await self._ensure_unique_values(table, column_name)

        # Create a descriptive name for the index
        # Format: uq_[table_name]_[col1]_[col2]_etc
        index_name = f"uq_{sanitized_table_name}_{sanitized_column}"

        # Get database connection
        conn = await self.session.connection()

        index_expression = f"(data ->> '{sanitized_column}')"
        ddl = f"CREATE UNIQUE INDEX {index_name} ON {full_table_name} ({index_expression})"
        await conn.execute(sa.DDL(ddl))

        # Commit the transaction
        await self.session.flush()

    @require_access_level(AccessLevel.ADMIN)
    async def _remove_jsonb_key(self, table: Table, key: str) -> None:
        """Remove a key from all rows' data JSONB column."""
        context = self._table_context(table)
        conn = await self.session.connection()
        sanitized_key = self._sanitize_identifier(key)
        key_literal = sa.literal(sanitized_key)

        stmt = (
            sa.update(context.table)
            .values(data=context.data_column.op("-")(key_literal))
            .where(context.data_column.has_key(sanitized_key))
        )
        await conn.execute(stmt)

    async def delete_column(self, column: TableColumn) -> None:
        """Remove a column from an existing table."""
        # Get table before deleting column metadata
        table = await self.get_table(column.table_id)

        existing_index_columns = await self.get_index(table)
        if column.name in existing_index_columns:
            await self._drop_unique_index(table, column.name)

        # Cascade: remove the key from all rows' data
        await self._remove_jsonb_key(table, column.name)

        # Delete the column metadata
        await self.session.delete(column)
        await self.session.flush()

    """Rows"""

    async def list_rows(
        self, table: Table, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List all rows in a table."""
        context = self._table_context(table)
        conn = await self.session.connection()
        stmt = sa.select("*").select_from(context.table).limit(limit).offset(offset)
        result = await conn.execute(stmt)
        return [self._flatten_record(row) for row in result.mappings().all()]

    async def get_row(self, table: Table, row_id: UUID) -> Any:
        """Get a row by ID."""
        context = self._table_context(table)
        conn = await self.session.connection()
        stmt = (
            sa.select("*").select_from(context.table).where(sa.column("id") == row_id)
        )
        result = await conn.execute(stmt)
        row = result.mappings().first()
        if row is None:
            raise TracecatNotFoundError(f"Row {row_id} not found in table {table.name}")
        return self._flatten_record(row)

    # Helper for fetching case-linked rows
    async def get_rows_by_ids(
        self, table: Table, row_ids: Sequence[UUID | str]
    ) -> dict[UUID, dict[str, Any]]:
        """Fetch multiple rows by ID in a single query."""
        if not row_ids:
            return {}

        normalised_ids: list[UUID] = []
        seen: set[UUID] = set()
        for raw_id in row_ids:
            try:
                resolved = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
                self.logger.warning(
                    "Skipping invalid row identifier during batch fetch",
                    table=table.name,
                    row_id=raw_id,
                    error=str(exc),
                )
                continue
            if resolved not in seen:
                seen.add(resolved)
                normalised_ids.append(resolved)

        if not normalised_ids:
            return {}

        context = self._table_context(table)
        conn = await self.session.connection()
        stmt = (
            sa.select("*")
            .select_from(context.table)
            .where(context.table.c.id.in_(normalised_ids))
        )

        result = await conn.execute(stmt)
        rows: dict[UUID, dict[str, Any]] = {}
        for mapping in result.mappings().all():
            flattened = self._flatten_record(mapping)
            raw_id = flattened.get("id")
            if raw_id is None:
                continue
            try:
                resolved_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
            except (TypeError, ValueError):
                continue
            rows[resolved_id] = flattened
        return rows

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
        context = self._table_context(table)
        conn = await self.session.connection()

        row_data = self._normalize_row_inputs(table, params.data, include_defaults=True)
        upsert = params.upsert

        table_name_for_logging = table.name
        record = {"data": row_data}

        if not upsert:
            stmt = sa.insert(context.table).values(record).returning(sa.text("*"))
            try:
                result = await conn.execute(stmt)
                await self.session.flush()
                row = result.mappings().one()
                return self._flatten_record(row)
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

        # For upsert operations
        index = await self.get_index(table)

        # Check if we have any unique columns to use for conflict resolution
        if not index:
            raise ValueError("Table must have at least one unique index for upsert")

        if len(index) > 1:
            raise ValueError(
                "Table cannot have multiple unique indexes. This is an unexpected error. Please contact support."
            )

        index_column = self._sanitize_identifier(index[0])

        # Ensure the conflict key is actually in the data
        if index_column not in row_data:
            raise ValueError("Data to upsert must contain the unique index column")

        pg_stmt = insert(context.table).values(record)
        index_expression = sa.text(f"(data ->> '{index_column}')")

        try:
            stmt = pg_stmt.on_conflict_do_update(
                index_elements=[index_expression],
                set_={
                    "data": sa.func.coalesce(
                        context.data_column,
                        sa.text("'{}'::jsonb"),
                    ).op("||")(pg_stmt.excluded.data),
                    "updated_at": sa.func.now(),
                },
            ).returning(sa.text("*"))

            result = await conn.execute(stmt)
            await self.session.flush()
            row = result.mappings().one()
            return self._flatten_record(row)
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
            if "no unique or exclusion constraint matching the ON CONFLICT" in str(e):
                raise ValueError(
                    "Please check that the unique index columns are present in the data"
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
        context = self._table_context(table)
        conn = await self.session.connection()

        # Normalise inputs and build update statement using SQLAlchemy
        normalised_data = self._normalize_row_inputs(table, data)
        payload_param = sa.bindparam("payload", type_=JSONB)
        stmt = (
            sa.update(context.table)
            .where(sa.column("id") == row_id)
            .values(
                data=sa.func.coalesce(
                    context.data_column,
                    sa.text("'{}'::jsonb"),
                ).op("||")(payload_param),
                updated_at=sa.func.now(),
            )
            .returning(sa.text("*"))
        )

        result = await conn.execute(stmt, {"payload": normalised_data})
        await self.session.flush()

        try:
            row = result.mappings().one()
        except NoResultFound:
            raise TracecatNotFoundError(
                f"Row {row_id} not found in table {table.name}"
            ) from None

        return self._flatten_record(row)

    @require_access_level(AccessLevel.ADMIN)
    async def delete_row(self, table: Table, row_id: UUID) -> None:
        """Delete a row from the table and cascade delete any case links."""
        context = self._table_context(table)

        async with self.session.begin_nested():
            await self.session.exec(
                sa.delete(CaseTableRow).where(
                    CaseTableRow.table_id == table.id,
                    CaseTableRow.row_id == row_id,
                )
            )

            await self.session.exec(
                sa.delete(context.table).where(context.table.c.id == row_id)
            )

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

        table = await self.get_table_by_name(table_name)
        context = self._table_context(table)
        column_index = self._column_index(table)
        match_payload: dict[str, Any] = {}
        for column, value in zip(columns, values, strict=True):
            sanitized_col = self._sanitize_identifier(column)
            column_model = column_index.get(sanitized_col)
            if column_model is None:
                raise ValueError(
                    f"Column '{column}' does not exist in table '{table_name}'"
                )
            match_payload[sanitized_col] = self._normalise_value(column_model, value)

        stmt = (
            sa.select(sa.text("*"))
            .select_from(context.table)
            .where(context.data_column.contains(match_payload))
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        # Use connection directly instead of begin() to avoid transaction conflicts
        conn = await self.session.connection()
        try:
            result = await conn.execute(
                stmt,
                execution_options={
                    "isolation_level": "READ COMMITTED",
                },
            )
            return [self._flatten_record(row) for row in result.mappings().all()]
        except _RETRYABLE_DB_EXCEPTIONS as e:
            # Log the error for debugging
            self.logger.warning(
                "Retryable DB exception occurred",
                kind=type(e).__name__,
                error=str(e),
                table=table_name,
                schema=context.schema,
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
                schema=context.schema,
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

        table = await self.get_table_by_name(table_name)
        context = self._table_context(table)
        column_index = self._column_index(table)
        match_payload: dict[str, Any] = {}
        for column, value in zip(columns, values, strict=True):
            sanitized_col = self._sanitize_identifier(column)
            column_model = column_index.get(sanitized_col)
            if column_model is None:
                raise ValueError(
                    f"Column '{column}' does not exist in table '{table_name}'"
                )
            match_payload[sanitized_col] = self._normalise_value(column_model, value)

        stmt = (
            sa.select(sa.literal(True))
            .select_from(context.table)
            .where(context.data_column.contains(match_payload))
            .limit(1)
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
                exists_val = result.scalar()
                return bool(exists_val)
            except _RETRYABLE_DB_EXCEPTIONS as e:
                self.logger.warning(
                    "Retryable DB exception occurred during exists_rows",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=context.schema,
                )
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
                    "Unexpected DB exception occurred during exists_rows",
                    kind=type(e).__name__,
                    error=str(e),
                    table=table_name,
                    schema=context.schema,
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
        stmt, context = self._row_select(
            table,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
        )
        conn = await self.session.connection()
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset > 0:
            stmt = stmt.offset(offset)

        try:
            result = await conn.execute(stmt)
            return [self._flatten_record(row) for row in result.mappings().all()]
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
                schema=context.schema,
            )
            raise

    async def list_rows_paginated(
        self,
        table: Table,
        params: CursorPaginationParams,
        search_term: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
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

        Returns:
            Cursor paginated response with matching rows

        Raises:
            TracecatNotFoundError: If the table does not exist
            ValueError: If search parameters are invalid
        """
        base_stmt, context = self._row_select(
            table,
            search_term=search_term,
            start_time=start_time,
            end_time=end_time,
            updated_before=updated_before,
            updated_after=updated_after,
        )
        stmt = base_stmt
        conn = await self.session.connection()

        cursor_data = None
        if params.cursor:
            try:
                cursor_data = BaseCursorPaginator.decode_cursor(params.cursor)
            except Exception as e:
                raise ValueError(f"Invalid cursor: {e}") from e

            # Apply cursor filtering for table rows
            cursor_time = cursor_data.created_at
            cursor_id = UUID(cursor_data.id)

            if params.reverse:
                # For reverse pagination (going backwards)
                stmt = stmt.where(
                    sa.or_(
                        sa.column("created_at") > cursor_time,
                        sa.and_(
                            sa.column("created_at") == cursor_time,
                            sa.column("id") > cursor_id,
                        ),
                    )
                )
            else:
                # For forward pagination (going forwards)
                stmt = stmt.where(
                    sa.or_(
                        sa.column("created_at") < cursor_time,
                        sa.and_(
                            sa.column("created_at") == cursor_time,
                            sa.column("id") < cursor_id,
                        ),
                    )
                )

        # Apply consistent ordering for cursor pagination
        if params.reverse:
            # For reverse pagination, use ASC ordering
            stmt = stmt.order_by(sa.column("created_at").asc(), sa.column("id").asc())
        else:
            # For forward pagination, use DESC ordering (newest first)
            stmt = stmt.order_by(sa.column("created_at").desc(), sa.column("id").desc())

        # Fetch limit + 1 to determine if there are more items
        stmt = stmt.limit(params.limit + 1)

        try:
            result = await conn.execute(stmt)
            rows = [self._flatten_record(row) for row in result.mappings().all()]
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
                schema=context.schema,
            )
            raise

        # Check if there are more items
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        # Generate cursors
        next_cursor = None
        prev_cursor = None

        if rows:
            if has_more:
                # Generate next cursor from the last item
                last_item = rows[-1]
                next_cursor = BaseCursorPaginator.encode_cursor(
                    last_item["created_at"], last_item["id"]
                )

            if params.cursor:
                # If we used a cursor to get here, we can go back
                first_item = rows[0]
                prev_cursor = BaseCursorPaginator.encode_cursor(
                    first_item["created_at"], first_item["id"]
                )

        # If we were doing reverse pagination, swap the cursors and reverse items
        if params.reverse:
            rows = list(reversed(rows))
            next_cursor, prev_cursor = prev_cursor, next_cursor

        return CursorPaginatedResponse(
            items=rows,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=params.cursor is not None,
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

        context = self._table_context(table)
        normalised_rows = [self._normalize_row_inputs(table, row) for row in rows]
        payload = [{"data": row} for row in normalised_rows]
        conn = await self.session.connection()

        if not upsert:
            stmt = sa.insert(context.table).values(payload)
            try:
                result = await conn.execute(stmt)
            except Exception as e:
                raise DBAPIError("Failed to insert batch", str(e), e) from e
            await self.session.flush()
            return result.rowcount

        index = await self.get_index(table)
        if not index:
            raise ValueError("Table must have at least one unique index for upsert")
        if len(index) > 1:
            raise ValueError(
                "Table cannot have multiple unique indexes. This is an unexpected error. Please contact support."
            )

        sanitized_index_col = self._sanitize_identifier(index[0])
        for row in normalised_rows:
            if sanitized_index_col not in row:
                raise ValueError(
                    "Each row to upsert must contain the unique index column"
                )

        pg_stmt = insert(context.table).values(payload)
        index_expression = sa.text(f"(data ->> '{sanitized_index_col}')")
        stmt = pg_stmt.on_conflict_do_update(
            index_elements=[index_expression],
            set_={
                "data": sa.func.coalesce(
                    context.data_column,
                    sa.text("'{}'::jsonb"),
                ).op("||")(pg_stmt.excluded.data),
                "updated_at": sa.func.now(),
            },
        )

        try:
            result = await conn.execute(stmt)
        except Exception as e:
            raise DBAPIError("Failed to insert batch", str(e), e) from e

        await self.session.flush()
        return result.rowcount


class TablesService(BaseTablesService):
    """Transactional tables service."""

    async def create_table(self, params: TableCreate) -> Table:
        result = await super().create_table(params)
        await self.session.commit()
        await self.session.refresh(result)
        return result

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
            csv_text = contents.decode()
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
            await self.delete_table(table)
        except Exception as cleanup_error:
            logger.error(
                "Failed to clean up table after import failure",
                table_id=str(table.id),
                error=cleanup_error,
            )

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
            raise ValueError(f"Invalid type: SqlType.{params.type.name}")

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
    ) -> list[dict[str, Any]]:
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
            column_info = col_map.get(col)
            if column_info is None:
                raise ValueError(
                    f"Column '{col}' does not exist in table {self.table_name}"
                )
            column_type = column_info["type"]
            coerced_value = (
                coerce_to_utc_datetime(value)
                if value is not None and getattr(column_type, "timezone", False)
                else value
            )
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
            coerced_value = (
                coerce_to_utc_datetime(value)
                if value is not None and getattr(column_type, "timezone", False)
                else value
            )
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
    if not sanitized:
        raise ValueError(
            "Identifier must contain at least one alphanumeric character or underscore."
        )
    if not (sanitized[0].isalpha() or sanitized[0] == "_"):
        raise ValueError("Identifier must start with a letter or underscore")
    return sanitized.lower()
