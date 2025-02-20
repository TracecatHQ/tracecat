import textwrap
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlmodel import select

from tracecat.authz.controls import require_access_level
from tracecat.db.schemas import Table, TableColumn
from tracecat.identifiers import TableColumnID, TableID
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.logger import logger
from tracecat.service import BaseService
from tracecat.tables.models import (
    TableColumnCreate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatNotFoundError


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

    def _get_schema_name(self, workspace_id: WorkspaceUUID) -> str:
        """Generate the schema name for a workspace."""
        # Using double quotes to allow dots in schema name
        return f'"tables.{workspace_id.short()}"'

    def _full_table_name(
        self, table_name: str, workspace_id: WorkspaceUUID | None = None
    ) -> str:
        """Get the full table name for a table."""
        ws_id = workspace_id or self._workspace_id()
        schema_name = self._get_schema_name(ws_id)
        return f"{schema_name}.{table_name}"

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
        """Create a new lookup table."""
        ws_id = self._workspace_id()
        schema_name = self._get_schema_name(ws_id)

        # Create schema if it doesn't exist
        conn = await self.session.connection()
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))

        # Create the table with just id and timestamp columns initially
        table_name = self._sanitize_identifier(params.name)
        create_table_sql = textwrap.dedent(f"""
        CREATE TABLE IF NOT EXISTS {schema_name}.{table_name} (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
        logger.info(f"Creating table {table_name} with SQL:\n{create_table_sql}")

        # Create the physical table
        await conn.execute(text(create_table_sql))

        # Create metadata entry
        metadata = Table(owner_id=ws_id, name=table_name)
        self.session.add(metadata)
        await self.session.commit()
        await self.session.refresh(metadata)

        return metadata

    @require_access_level(AccessLevel.ADMIN)
    async def update_table(self, table: Table, params: TableUpdate) -> Table:
        """Update a lookup table."""
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(table, key, value)

        await self.session.commit()
        await self.session.refresh(table)
        return table

    @require_access_level(AccessLevel.ADMIN)
    async def delete_table(self, table: Table):
        """Delete a lookup table."""
        # Delete the metadata first
        await self.session.delete(table)

        # Drop the actual table
        full_table_name = self._full_table_name(table.name)
        conn = await self.session.connection()
        await conn.execute(text(f"DROP TABLE IF EXISTS {full_table_name}"))

    """Columns"""

    async def get_column(self, table: Table, column_id: TableColumnID) -> TableColumn:
        """Get a column by ID."""
        statement = select(TableColumn).where(
            TableColumn.table_id == table.id,
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
        """Add a new column to an existing table."""
        column_name = self._sanitize_identifier(params.name)
        full_table_name = self._full_table_name(table.name)

        # Create the column metadata first
        column = TableColumn(
            table_id=table.id,
            name=column_name,
            type=params.type,
            nullable=params.nullable,
            default=params.default,
        )
        self.session.add(column)

        # Add the column to the physical table
        conn = await self.session.connection()
        nullable = "" if params.nullable else "NOT NULL"
        default = f"DEFAULT {params.default}" if params.default is not None else ""

        await conn.execute(
            text(
                f"ALTER TABLE {full_table_name} "
                f"ADD COLUMN {column_name} {params.type} {nullable} {default}"
            )
        )

        await self.session.commit()
        await self.session.refresh(column)
        return column

    @require_access_level(AccessLevel.ADMIN)
    async def delete_column(self, column: TableColumn) -> None:
        """Remove a column from an existing table."""
        ws_id = self._workspace_id()
        full_table_name = self._full_table_name(column.table.name, ws_id)
        sanitized_column = self._sanitize_identifier(column.name)

        # Delete the column metadata first
        await self.session.delete(column)

        # Drop the column from the physical table
        conn = await self.session.connection()
        await conn.execute(
            text(f"ALTER TABLE {full_table_name} DROP COLUMN {sanitized_column}")
        )

        await self.session.commit()

    """Rows"""

    async def get_row(self, table: Table, row_id: UUID) -> Any:
        """Get a row by ID."""
        full_table_name = self._full_table_name(table.name)
        conn = await self.session.connection()
        result = await conn.execute(
            text(f"SELECT * FROM {full_table_name} WHERE id = :row_id"),
            {"row_id": row_id},
        )
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
        full_table_name = self._full_table_name(table.name)

        data = params.data
        columns = ", ".join(self._sanitize_identifier(k) for k in data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())

        conn = await self.session.connection()
        result = await conn.execute(
            text(
                f"INSERT INTO {full_table_name} ({columns}) "
                f"VALUES ({placeholders}) RETURNING *"
            ),
            data,
        )
        await self.session.commit()
        # Return the full row as a mapping instead of just the scalar value
        row = result.mappings().one()
        return dict(row)

    async def update_row(
        self,
        table: Table,
        row_id: UUID,
        data: dict[str, Any],
    ) -> Mapping[str, Any] | None:
        """Update an existing row in the table.

        Args:
            workspace_id: The workspace ID where the table exists
            table_name: The name of the table
            row_id: The ID of the row to update
            data: Dictionary of column names and values to update

        Returns:
            The updated row data or None if update failed
        """
        full_table_name = self._full_table_name(table.name)

        set_clause = ", ".join(
            f"{self._sanitize_identifier(k)} = :{k}" for k in data.keys()
        )

        conn = await self.session.connection()
        result = await conn.execute(
            text(
                f"UPDATE {full_table_name} SET {set_clause} "
                f"WHERE id = :row_id RETURNING *"
            ),
            {**data, "row_id": row_id},
        )
        await self.session.commit()
        row = result.mappings().one()
        return dict(row)

    "Lookups"

    async def lookup_row(
        self,
        table_name: str,
        *,
        columns: Sequence[str],
        values: Sequence[Any],
    ) -> Sequence[Mapping[str, Any]]:
        """Lookup a value in a table.

        This should absolutely be cached in the future.
        """
        if len(values) != len(columns):
            raise ValueError("Values and column names must have the same length")
        full_table_name = self._full_table_name(table_name)
        conn = await self.session.connection()
        result = await conn.execute(
            text(
                f"SELECT * FROM {full_table_name} WHERE "
                f"{' AND '.join(f'{c} = :{c}' for c in columns)}"
            ),
            dict(zip(columns, values, strict=True)),
        )
        # Convert SQLAlchemy RowMapping objects to plain dictionaries
        return [dict(row) for row in result.mappings().all()]


async def main():
    role = Role(
        type="user",
        workspace_id=UUID("3ef66353-b08d-4848-bd2e-c2fc14b6eae1"),
        service_id="tracecat-api",
        access_level=AccessLevel.ADMIN,
    )
    async with TablesService.with_session(role=role) as service:
        # Create table
        table = await service.get_table_by_name("test")
        print(table)
        # # Create column
        # column = await service.create_column(
        #     table, TableColumnCreate(name="name", type="TEXT")
        # )
        # print(column)
        column = await service.create_column(
            table, TableColumnCreate(name="age", type="INTEGER")
        )
        print(column)

        # Add row
        row = await service.insert_row(
            table, TableRowInsert(data={"name": "John", "age": 30})
        )
        print(row)
        # Lookup
        print(
            await service.lookup_row(
                "test", columns=["name", "age"], values=["John", 30]
            )
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
