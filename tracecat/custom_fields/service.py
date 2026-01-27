from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateSchema, CreateTable, DropSchema

from tracecat.auth.types import Role
from tracecat.custom_fields.schemas import CustomFieldCreate, CustomFieldUpdate
from tracecat.db.locks import derive_lock_key_from_parts, pg_advisory_lock
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TableEditorService, sanitize_identifier


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

    @abstractmethod
    def _table_definition(self) -> sa.Table:
        """Return the SQLAlchemy Table definition for the workspace table.

        Subclasses must implement this to define the table structure.
        """

        raise NotImplementedError

    async def initialize_workspace_schema(self) -> None:
        """Create the workspace schema and base table if absent."""

        lock_key = derive_lock_key_from_parts(
            "tracecat.custom_fields.initialize_workspace_schema",
            self.service_name,
            self._table,
        )
        async with pg_advisory_lock(self.session, lock_key):
            await self.session.execute(
                CreateSchema(self.schema_name, if_not_exists=True)
            )
            await self.session.execute(
                CreateTable(self._table_definition(), if_not_exists=True)
            )
        self._schema_initialized = True

    async def _ensure_schema_ready(self) -> None:
        """Ensure the workspace schema exists, creating it if needed."""

        if self._schema_initialized:
            return

        conn = await self.session.connection()

        def check_schema_exists(sync_conn: sa.Connection) -> bool:
            inspector = sa.inspect(sync_conn)
            return inspector.has_schema(self.schema_name) and inspector.has_table(
                self._table, schema=self.schema_name
            )

        if await conn.run_sync(check_schema_exists):
            self._schema_initialized = True
            return

        await self.initialize_workspace_schema()

    async def drop_workspace_schema(self) -> None:
        """Drop the workspace schema and all contained objects."""

        await self.session.execute(
            DropSchema(self.schema_name, cascade=True, if_exists=True)
        )
        self._schema_initialized = False

    async def list_fields(self) -> Sequence[sa.engine.interfaces.ReflectedColumn]:
        """List all custom fields for the workspace."""

        await self._ensure_schema_ready()
        return await self.editor.get_columns()

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
            ValueError: If the field is a reserved column
        """

        await self._ensure_schema_ready()
        if field_id in self._reserved_columns:
            raise ValueError(f"Field {field_id} is a reserved field")
        await self.editor.delete_column(field_id)
        await self.session.commit()


__all__ = ["CustomFieldsService"]
