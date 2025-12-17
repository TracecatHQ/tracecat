"""Tables SDK client for Tracecat API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class TablesClient:
    """Client for Tables API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    # === Table CRUD === #

    async def get_table_by_name(self, name: str) -> dict[str, Any]:
        """Get a table by name.

        Args:
            name: The table name.

        Returns:
            Table metadata with columns.
        """
        return await self._client.get(f"/tables/by-name/{name}")

    async def list_tables(self) -> list[dict[str, Any]]:
        """List all tables in the workspace.

        Returns:
            List of table metadata.
        """
        return await self._client.get("/tables")

    async def create_table(
        self,
        *,
        name: str,
        columns: list[dict[str, Any]] | Unset = UNSET,
    ) -> dict[str, Any]:
        """Create a new table.

        Args:
            name: Table name.
            columns: Column definitions (name, type, nullable, default).

        Returns:
            Created table data.
        """
        data: dict[str, Any] = {"name": name}
        if is_set(columns):
            data["columns"] = columns

        return await self._client.post("/tables", json=data)

    async def get_table(self, table_id: str) -> dict[str, Any]:
        """Get a table by ID.

        Args:
            table_id: The table UUID.

        Returns:
            Table metadata with columns.
        """
        return await self._client.get(f"/tables/{table_id}")

    async def update_table(
        self,
        table_id: str,
        *,
        name: str | Unset = UNSET,
    ) -> None:
        """Update table metadata.

        Args:
            table_id: The table UUID.
            name: New table name.
        """
        data: dict[str, Any] = {}
        if is_set(name):
            data["name"] = name

        await self._client.patch(f"/tables/{table_id}", json=data)

    async def delete_table(self, table_id: str) -> None:
        """Delete a table.

        Args:
            table_id: The table UUID.
        """
        await self._client.delete(f"/tables/{table_id}")

    # === Column Operations === #

    async def create_column(
        self,
        table_id: str,
        *,
        name: str,
        column_type: str,
        nullable: bool = True,
        default: Any | Unset = UNSET,
    ) -> dict[str, Any]:
        """Add a column to a table.

        Args:
            table_id: The table UUID.
            name: Column name.
            column_type: SQL type (text, integer, boolean, etc.).
            nullable: Whether the column allows NULL values.
            default: Default value for the column.

        Returns:
            Created column data.
        """
        data: dict[str, Any] = {
            "name": name,
            "type": column_type,
            "nullable": nullable,
        }
        if is_set(default):
            data["default"] = default

        return await self._client.post(f"/tables/{table_id}/columns", json=data)

    async def update_column(
        self,
        table_id: str,
        column_id: str,
        *,
        name: str | Unset = UNSET,
    ) -> None:
        """Update a column.

        Args:
            table_id: The table UUID.
            column_id: The column ID.
            name: New column name.
        """
        data: dict[str, Any] = {}
        if is_set(name):
            data["name"] = name

        await self._client.patch(f"/tables/{table_id}/columns/{column_id}", json=data)

    async def delete_column(self, table_id: str, column_id: str) -> None:
        """Delete a column from a table.

        Args:
            table_id: The table UUID.
            column_id: The column ID.
        """
        await self._client.delete(f"/tables/{table_id}/columns/{column_id}")

    # === Row Operations === #

    async def list_rows(
        self,
        table_id: str,
        *,
        limit: int = 20,
        cursor: str | Unset = UNSET,
        reverse: bool = False,
        order_by: str | Unset = UNSET,
        sort: Literal["asc", "desc"] | Unset = UNSET,
    ) -> dict[str, Any]:
        """List rows in a table with pagination.

        Args:
            table_id: The table UUID.
            limit: Maximum rows per page.
            cursor: Pagination cursor.
            reverse: Reverse pagination direction.
            order_by: Column to order by.
            sort: Sort direction.

        Returns:
            Paginated list of rows.
        """
        params: dict[str, Any] = {"limit": limit}
        if is_set(cursor):
            params["cursor"] = cursor
        if reverse:
            params["reverse"] = reverse
        if is_set(order_by):
            params["order_by"] = order_by
        if is_set(sort):
            params["sort"] = sort

        return await self._client.get(f"/tables/{table_id}/rows", params=params)

    async def get_row(self, table_id: str, row_id: str) -> dict[str, Any]:
        """Get a row by ID.

        Args:
            table_id: The table UUID.
            row_id: The row UUID.

        Returns:
            Row data.
        """
        return await self._client.get(f"/tables/{table_id}/rows/{row_id}")

    async def insert_row(
        self,
        table_id: str,
        *,
        data: dict[str, Any],
        upsert: bool = False,
    ) -> dict[str, Any]:
        """Insert a row into a table.

        Args:
            table_id: The table UUID.
            data: Row data as column name to value mapping.
            upsert: If True, update existing row on conflict based on unique index.

        Returns:
            Created row data.
        """
        return await self._client.post(
            f"/tables/{table_id}/rows",
            json={"data": data, "upsert": upsert},
        )

    async def batch_insert_rows(
        self,
        table_id: str,
        *,
        rows: list[dict[str, Any]],
        upsert: bool = False,
    ) -> dict[str, Any]:
        """Insert multiple rows atomically.

        Args:
            table_id: The table UUID.
            rows: List of row data dicts.
            upsert: If True, update existing rows on conflict based on unique index.

        Returns:
            Response with rows_inserted count.
        """
        return await self._client.post(
            f"/tables/{table_id}/rows/batch",
            json={"rows": rows, "upsert": upsert},
        )

    async def delete_row(self, table_id: str, row_id: str) -> None:
        """Delete a row from a table.

        Args:
            table_id: The table UUID.
            row_id: The row UUID.
        """
        await self._client.delete(f"/tables/{table_id}/rows/{row_id}")

    async def update_row(
        self,
        table_id: str,
        row_id: str,
        *,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a row in a table.

        Args:
            table_id: The table UUID.
            row_id: The row UUID.
            data: Row data as column name to value mapping.

        Returns:
            Updated row data.
        """
        return await self._client.patch(
            f"/tables/{table_id}/rows/{row_id}",
            json={"data": data},
        )

    async def search_rows(
        self,
        table_id: str,
        *,
        search_term: str | Unset = UNSET,
        start_time: datetime | Unset = UNSET,
        end_time: datetime | Unset = UNSET,
        updated_before: datetime | Unset = UNSET,
        updated_after: datetime | Unset = UNSET,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search for rows in a table with optional filtering.

        Args:
            table_id: The table UUID.
            search_term: Text to search for across all text and JSONB columns.
            start_time: Filter rows created after this time.
            end_time: Filter rows created before this time.
            updated_before: Filter rows updated before this time.
            updated_after: Filter rows updated after this time.
            offset: Number of rows to skip.
            limit: Maximum number of rows to return.

        Returns:
            List of matching rows.
        """
        data: dict[str, Any] = {
            "offset": offset,
            "limit": limit,
        }
        if is_set(search_term):
            data["search_term"] = search_term
        if is_set(start_time):
            data["start_time"] = start_time.isoformat()
        if is_set(end_time):
            data["end_time"] = end_time.isoformat()
        if is_set(updated_before):
            data["updated_before"] = updated_before.isoformat()
        if is_set(updated_after):
            data["updated_after"] = updated_after.isoformat()

        return await self._client.post(f"/tables/{table_id}/rows/search", json=data)

    async def download(
        self,
        table_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Download table data as a list of rows.

        Args:
            table_id: The table UUID.
            limit: Maximum number of rows to return.

        Returns:
            List of rows.
        """
        return await self._client.get(
            f"/tables/{table_id}/download",
            params={"limit": limit},
        )

    async def lookup(
        self,
        *,
        table_name: str,
        column: str,
        value: Any,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Lookup rows by column value.

        Args:
            table_name: Table name (not ID).
            column: Column to search.
            value: Value to match.
            limit: Maximum rows to return.

        Returns:
            List of matching rows.
        """
        params: dict[str, Any] = {
            "table": table_name,
            "column": column,
            "value": value,
            "limit": limit,
        }
        return await self._client.get("/tables/lookup", params=params)

    async def exists(
        self,
        *,
        table_name: str,
        column: str,
        value: Any,
    ) -> bool:
        """Check if a value exists in a table column.

        Args:
            table_name: Table name (not ID).
            column: Column to search.
            value: Value to check.

        Returns:
            True if value exists.
        """
        params: dict[str, Any] = {
            "table": table_name,
            "column": column,
            "value": value,
        }
        result = await self._client.get("/tables/exists", params=params)
        return result.get("exists", False)

    async def lookup_many(
        self,
        *,
        table_name: str,
        column: str,
        values: list[Any],
        limit: int | Unset = UNSET,
    ) -> list[dict[str, Any]]:
        """Lookup rows by multiple column values.

        Args:
            table_name: Table name (not ID).
            column: Column to search.
            values: Values to match (OR logic).
            limit: Maximum rows to return.

        Returns:
            List of matching rows.
        """
        params: dict[str, Any] = {
            "table": table_name,
            "column": column,
            "values": values,
        }
        if is_set(limit):
            params["limit"] = limit
        return await self._client.get("/tables/lookup-many", params=params)
