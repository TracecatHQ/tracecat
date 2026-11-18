from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateSchema, CreateTable, DropSchema

from tracecat.auth.types import Role
from tracecat.custom_fields.schemas import CustomFieldCreate, CustomFieldUpdate
from tracecat.custom_fields.types import CustomFieldColumn
from tracecat.db.locks import derive_lock_key_from_parts, pg_advisory_xact_lock
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import (
    DYNAMIC_WORKSPACE_RLS_POLICY,
    DYNAMIC_WORKSPACE_TENANT_COLUMN,
    RLS_BYPASS_ON,
    RLS_BYPASS_VAR,
    RLS_WORKSPACE_VAR,
    TableEditorService,
    assign_dynamic_owner,
    is_internal_column_name,
    sanitize_identifier,
    validate_identifier,
)


def _columns_sql(full_table_name: str) -> sa.TextClause:
    """Read a table's columns from the catalog, in table order.

    The statement also reads from the table itself (zero rows), so Postgres
    takes the ACCESS SHARE lock a SELECT takes before the catalog read runs.
    Column DDL needs ACCESS EXCLUSIVE, so the columns returned stay current
    until the caller's transaction ends, and a statement that waited behind
    DDL sees the committed result.
    """
    return sa.text(
        f"""
        SELECT a.attname AS name,
            format_type(a.atttypid, NULL) AS type,
            NOT a.attnotnull AS nullable,
            pg_get_expr(d.adbin, d.adrelid) AS column_default,
            col_description(a.attrelid, a.attnum) AS comment
        FROM pg_attribute AS a
        LEFT JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN (SELECT 1 FROM {full_table_name} LIMIT 0) AS table_lock ON true
        WHERE a.attrelid = CAST(:table_name AS regclass)
            AND a.attnum > 0
            AND NOT a.attisdropped
        ORDER BY a.attnum
        """
    )


class CustomFieldsService(BaseWorkspaceService, ABC):
    """Base service for managing workspace-specific custom fields.

    Subclasses must define:
        - service_name: Identifier for the service
        - _table: The SQLAlchemy table name
        - _schema_prefix: Prefix for the workspace schema
        - _reserved_columns: Set of column names that cannot be deleted
        - initialize_workspace_schema(): Create the schema and base table
    """

    service_name: ClassVar[str]
    _table: ClassVar[str]
    _schema_prefix: ClassVar[str] = "custom_fields_"
    _reserved_columns: ClassVar[set[str]]

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self._workspace_uuid = WorkspaceUUID.new(self.workspace_id)
        self.schema_name = self._get_schema_name()
        self._schema_initialized = False
        self.sanitized_table_name = sanitize_identifier(self._table)
        self.editor = TableEditorService(
            self.session,
            self.role,
            table_name=self._table,
            schema_name=self.schema_name,
        )

    def _get_schema_name(self) -> str:
        """Generate the schema name for this workspace."""

        return f"{self._schema_prefix}{self._workspace_uuid.short()}"

    def _full_table_name(self) -> str:
        """Get the fully-qualified workspace table name."""
        return f'"{self.schema_name}".{self.sanitized_table_name}'

    def _workspace_tenant_default_sql(self) -> sa.TextClause:
        """Build the server default expression for the tenant column."""
        return sa.text(f"'{self._workspace_uuid}'::uuid")

    async def _enable_workspace_rls_for_base_table(self) -> None:
        """Enable workspace RLS policy on the workspace base table."""
        conn = await self.session.connection()
        policy_expr = (
            f"current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}' "
            f'OR "{DYNAMIC_WORKSPACE_TENANT_COLUMN}" = '
            f"NULLIF(current_setting('{RLS_WORKSPACE_VAR}', true), '')::uuid"
        )
        full_table_name = self._full_table_name()
        await conn.execute(
            sa.DDL("ALTER TABLE %s ENABLE ROW LEVEL SECURITY", full_table_name)
        )
        # FORCE so the policy also applies when the app role owns the table.
        await conn.execute(
            sa.DDL("ALTER TABLE %s FORCE ROW LEVEL SECURITY", full_table_name)
        )
        # Keep an existing policy: the dynamic RLS guard (b4074a0896b5)
        # rejects the app role dropping it. Policy changes belong in migrations.
        policy_exists = await conn.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_policy "
                "WHERE polrelid = to_regclass(:table_name) AND polname = :policy)"
            ),
            {"table_name": full_table_name, "policy": DYNAMIC_WORKSPACE_RLS_POLICY},
        )
        if policy_exists:
            return
        await conn.execute(
            sa.DDL(
                f"""
                CREATE POLICY {DYNAMIC_WORKSPACE_RLS_POLICY} ON %s
                    FOR ALL
                    USING ({policy_expr})
                    WITH CHECK ({policy_expr})
                """,
                full_table_name,
            )
        )

    def _assert_user_field_name_allowed(self, field_name: str) -> None:
        """Reject operations on internal/system-managed field names."""
        if is_internal_column_name(field_name):
            raise ValueError(f"Field {field_name} is reserved for internal use")

    @abstractmethod
    def _table_definition(self) -> sa.Table:
        """Return the SQLAlchemy Table definition for the workspace table.

        Subclasses must implement this to define the table structure.
        """

        raise NotImplementedError

    def _schema_lock_key(self) -> int:
        """Advisory lock key that serializes setup of this workspace's table."""
        return derive_lock_key_from_parts(
            "tracecat.custom_fields.initialize_workspace_schema",
            self.service_name,
            self.schema_name,
            self._table,
        )

    async def _schema_exists(self) -> bool:
        """Return whether the workspace table exists, without creating it."""
        if self._schema_initialized:
            return True
        exists = await self.session.scalar(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": self._full_table_name()},
        )
        self._schema_initialized = exists is True
        return self._schema_initialized

    async def initialize_workspace_schema(self) -> None:
        """Create the workspace schema and base table if absent."""

        # Held until the caller's transaction ends, so a waiter sees committed DDL.
        await pg_advisory_xact_lock(self.session, self._schema_lock_key())
        await self.session.execute(CreateSchema(self.schema_name, if_not_exists=True))
        conn = await self.session.connection()
        await assign_dynamic_owner(conn, "SCHEMA", f'"{self.schema_name}"')
        await self.session.execute(
            CreateTable(self._table_definition(), if_not_exists=True)
        )
        await assign_dynamic_owner(conn, "TABLE", self._full_table_name())
        await self._enable_workspace_rls_for_base_table()
        self._schema_initialized = True

    async def _ensure_schema_ready(self) -> None:
        """Ensure the workspace schema exists, creating it if needed.

        Only a missing table takes the setup lock, so steady-state operations
        never wait on each other here.
        """

        if not await self._schema_exists():
            await self.initialize_workspace_schema()

    async def drop_workspace_schema(self) -> None:
        """Drop the workspace schema and all contained objects."""

        await self.session.execute(
            DropSchema(self.schema_name, cascade=True, if_exists=True)
        )
        self._schema_initialized = False

    async def get_columns(self) -> list[CustomFieldColumn] | None:
        """Return the table's user-visible columns, or None if it is missing.

        One catalog statement that also locks the table (see `_columns_sql`),
        so a later query in this transaction built from the returned names
        sees the same columns, unless this transaction changes them itself.
        """

        if not await self._schema_exists():
            return None
        table_name = self._full_table_name()
        result = await self.session.execute(
            _columns_sql(table_name), {"table_name": table_name}
        )
        return [
            CustomFieldColumn(
                name=row["name"],
                type=row["type"],
                nullable=row["nullable"],
                default=row["column_default"],
                comment=row["comment"],
            )
            for row in result.mappings()
            if not is_internal_column_name(row["name"])
        ]

    async def list_fields(self) -> list[CustomFieldColumn]:
        """List all custom fields for the workspace.

        A missing table has no fields; a read does not create it.
        """

        return await self.get_columns() or []

    async def create_field(self, params: CustomFieldCreate) -> None:
        """Create a new custom field column."""

        await self._ensure_schema_ready()
        params.nullable = True  # Custom fields remain nullable by default
        await self.editor.create_column(params)
        await self.session.commit()

    async def update_field(self, field_id: str, params: CustomFieldUpdate) -> None:
        """Update a custom field column."""

        await self._ensure_schema_ready()
        await self.editor.update_column(field_id, params)
        await self.session.commit()

    async def delete_field(self, field_id: str) -> None:
        """Delete a custom field.

        Args:
            field_id: The name of the field to delete

        Raises:
            TracecatValidationError: If the field is reserved or internal
        """

        await self._ensure_schema_ready()
        self._assert_user_field_name_allowed(field_id)
        if field_id in self._reserved_columns:
            raise TracecatValidationError(f"Field {field_id} is a reserved field")
        validate_identifier(field_id)
        await self.editor.delete_column(field_id)
        await self.session.commit()


__all__ = ["CustomFieldsService"]
