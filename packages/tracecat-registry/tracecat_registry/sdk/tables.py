"""Tables SDK client for Tracecat API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from tracecat_registry import types
from tracecat_registry.sdk.types import (
    UNSET,
    TableAggregation,
    Unset,
    is_set,
)

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class TablesClient:
    """Client for Tables API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def list_tables(self) -> list[types.Table]:
        """List tables in the workspace."""
        return await self._client.get("/tables")

    async def create_table(
        self,
        *,
        name: str,
        columns: list[dict[str, Any]] | Unset = UNSET,
        raise_on_duplicate: bool = True,
    ) -> types.Table:
        """Create a new table.

        Args:
            name: Table name.
            columns: Optional column definitions.
            raise_on_duplicate: If False, return existing table when duplicate.

        Returns:
            Created table metadata.
        """
        data: dict[str, Any] = {
            "name": name,
            "raise_on_duplicate": raise_on_duplicate,
        }
        if is_set(columns):
            data["columns"] = columns
        return await self._client.post("/tables", json=data)

    async def get_table_metadata(self, name: str) -> types.TableRead:
        """Get table metadata and columns by name."""
        return await self._client.get(f"/tables/{name}/metadata")

    async def lookup(
        self,
        *,
        table: str,
        column: str,
        value: Any,
    ) -> dict[str, Any] | None:
        """Lookup a single row by column value."""
        rows = await self._client.post(
            f"/tables/{table}/lookup",
            json={"columns": [column], "values": [value], "limit": 1},
        )
        if isinstance(rows, list):
            return rows[0] if rows else None
        if isinstance(rows, dict) and isinstance(rows.get("items"), list):
            items = cast(list[dict[str, Any]], rows["items"])
            return items[0] if items else None
        raise ValueError("Unexpected lookup response")

    async def lookup_many(
        self,
        *,
        table: str,
        column: str,
        value: Any,
        limit: int | Unset = UNSET,
        group_by: str | Unset = UNSET,
        agg: TableAggregation | Unset = UNSET,
        agg_field: str | Unset = UNSET,
        include_aggregation: bool = False,
    ) -> list[dict[str, Any]] | types.TableLookupResponse:
        """Lookup multiple rows by column value."""
        data: dict[str, Any] = {"columns": [column], "values": [value]}
        if is_set(limit):
            data["limit"] = limit
        if is_set(group_by):
            data["group_by"] = group_by
        if is_set(agg):
            data["agg"] = agg
        if is_set(agg_field):
            data["agg_field"] = agg_field
        rows = await self._client.post(f"/tables/{table}/lookup", json=data)
        if isinstance(rows, list):
            return rows
        if isinstance(rows, dict) and isinstance(rows.get("items"), list):
            if include_aggregation:
                return cast(types.TableLookupResponse, rows)
            return cast(list[dict[str, Any]], rows["items"])
        raise ValueError("Unexpected lookup response")

    async def exists(
        self,
        *,
        table: str,
        column: str,
        value: Any,
    ) -> bool:
        """Check if a value exists in a table column."""
        return await self._client.post(
            f"/tables/{table}/exists",
            json={"columns": [column], "values": [value]},
        )

    async def search_rows(
        self,
        *,
        table: str,
        search_term: str | Unset = UNSET,
        start_time: datetime | str | Unset = UNSET,
        end_time: datetime | str | Unset = UNSET,
        updated_before: datetime | str | Unset = UNSET,
        updated_after: datetime | str | Unset = UNSET,
        cursor: str | Unset = UNSET,
        reverse: bool | Unset = UNSET,
        limit: int | Unset = UNSET,
    ) -> types.TableSearchResponse | list[dict[str, Any]]:
        """Search rows with optional filters."""
        data: dict[str, Any] = {}
        if is_set(search_term):
            data["search_term"] = search_term
        if is_set(start_time):
            data["start_time"] = (
                start_time.isoformat()
                if isinstance(start_time, datetime)
                else start_time
            )
        if is_set(end_time):
            data["end_time"] = (
                end_time.isoformat() if isinstance(end_time, datetime) else end_time
            )
        if is_set(updated_before):
            data["updated_before"] = (
                updated_before.isoformat()
                if isinstance(updated_before, datetime)
                else updated_before
            )
        if is_set(updated_after):
            data["updated_after"] = (
                updated_after.isoformat()
                if isinstance(updated_after, datetime)
                else updated_after
            )
        if is_set(cursor):
            data["cursor"] = cursor
        if is_set(reverse):
            data["reverse"] = reverse
        if is_set(limit):
            data["limit"] = limit
        response = await self._client.post(f"/tables/{table}/search", json=data)
        if isinstance(response, list):
            return cast(list[dict[str, Any]], response)
        if not isinstance(response, dict) or not isinstance(
            response.get("items"), list
        ):
            raise ValueError("Unexpected search response")
        return cast(types.TableSearchResponse, response)

    async def insert_row(
        self,
        *,
        table: str,
        row_data: dict[str, Any],
        upsert: bool = False,
    ) -> dict[str, Any]:
        """Insert a row into a table."""
        return await self._client.post(
            f"/tables/{table}/rows",
            json={"data": row_data, "upsert": upsert},
        )

    async def insert_rows(
        self,
        *,
        table: str,
        rows_data: list[dict[str, Any]],
        upsert: bool = False,
    ) -> int:
        """Insert multiple rows into a table."""
        response = await self._client.post(
            f"/tables/{table}/rows/batch",
            json={"rows": rows_data, "upsert": upsert},
        )
        if isinstance(response, dict) and "rows_inserted" in response:
            return int(response["rows_inserted"])
        if isinstance(response, int):
            return response
        raise ValueError("Unexpected batch insert response")

    async def update_row(
        self,
        *,
        table: str,
        row_id: str,
        row_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a row in a table."""
        return await self._client.patch(
            f"/tables/{table}/rows/{row_id}",
            json={"data": row_data},
        )

    async def delete_row(self, *, table: str, row_id: str) -> None:
        """Delete a row from a table."""
        await self._client.delete(f"/tables/{table}/rows/{row_id}")

    async def download(
        self,
        *,
        table: str,
        format: Literal["json", "ndjson", "csv", "markdown"] | Unset = UNSET,
        limit: int | Unset = UNSET,
    ) -> list[dict[str, Any]] | str:
        """Download table data in the requested format."""
        params: dict[str, Any] = {}
        if is_set(format):
            params["format"] = format
        if is_set(limit):
            params["limit"] = limit
        return await self._client.get(
            f"/tables/{table}/download", params=params or None
        )
