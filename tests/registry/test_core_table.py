"""Tests for core.table UDFs using the registry SDK client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from tracecat_registry.core import table as core_table
from tracecat_registry.sdk import TracecatConflictError
from tracecat_registry.sdk.client import TracecatClient


@pytest.mark.anyio
async def test_lookup_calls_executor_lookup_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/lookup"
        assert params == {
            "table": "t",
            "column": "c",
            "value": "v",
            "limit": 1,
        }
        return [{"id": "row-1"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_table.lookup(table="t", column="c", value="v") == {"id": "row-1"}


@pytest.mark.anyio
async def test_is_in_uses_tables_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/exists"
        assert params == {"table": "t", "column": "c", "value": "v"}
        return {"exists": True}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_table.is_in(table="t", column="c", value="v") is True


@pytest.mark.anyio
async def test_lookup_many_validates_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Limit cannot be greater than"):
        await core_table.lookup_many(table="t", column="c", value="v", limit=1001)


@pytest.mark.anyio
async def test_search_rows_resolves_table_id_then_posts_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/by-name/my_table"
        return {"id": "table-1"}

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/table-1/rows/search"
        assert json == {
            "offset": 0,
            "limit": 100,
            "search_term": "foo",
            "start_time": now.isoformat(),
        }
        return [{"id": "row-1"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    rows = await core_table.search_rows(
        table="my_table",
        search_term="foo",
        start_time=now,
    )
    assert rows == [{"id": "row-1"}]


@pytest.mark.anyio
async def test_insert_rows_batches_and_returns_rows_inserted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/by-name/my_table"
        return {"id": "table-1"}

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/table-1/rows/batch"
        assert json == {
            "rows": [
                {"a": 1},
                {"a": 2},
            ],
            "upsert": False,
        }
        return {"rows_inserted": 2}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    inserted = await core_table.insert_rows(
        table="my_table",
        rows_data=[{"a": 1}, {"a": 2}],
    )
    assert inserted == 2


@pytest.mark.anyio
async def test_create_table_raises_on_duplicate_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/tables"
        raise TracecatConflictError(detail="dup")

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    with pytest.raises(ValueError, match="Table already exists"):
        await core_table.create_table(name="dup", raise_on_duplicate=True)


@pytest.mark.anyio
async def test_list_tables_gets_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables"
        return [{"id": "t1"}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_table.list_tables() == [{"id": "t1"}]


@pytest.mark.anyio
async def test_get_table_metadata_calls_get_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/by-name/my_table"
        return {"id": "table-1", "name": "my_table", "columns": []}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    assert await core_table.get_table_metadata(name="my_table") == {
        "id": "table-1",
        "name": "my_table",
        "columns": [],
    }


@pytest.mark.anyio
async def test_insert_row_resolves_table_id_then_posts_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        calls.append(("GET", path, params))
        assert path == "/tables/by-name/my_table"
        return {"id": "table-1"}

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        calls.append(("POST", path, json))
        assert path == "/tables/table-1/rows"
        assert json == {"data": {"a": 1}, "upsert": False}
        return {"id": "row-1", "a": 1}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    assert await core_table.insert_row(table="my_table", row_data={"a": 1}) == {
        "id": "row-1",
        "a": 1,
    }
    assert [c[0] for c in calls] == ["GET", "POST"]


@pytest.mark.anyio
async def test_update_row_resolves_table_id_then_patches_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/by-name/my_table"
        return {"id": "table-1"}

    async def fake_patch(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/table-1/rows/row-1"
        assert json == {"data": {"a": 2}}
        return {"id": "row-1", "a": 2}

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "patch", fake_patch, raising=True)

    assert await core_table.update_row(
        table="my_table", row_id="row-1", row_data={"a": 2}
    ) == {
        "id": "row-1",
        "a": 2,
    }


@pytest.mark.anyio
async def test_delete_row_resolves_table_id_then_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/by-name/my_table"
        return {"id": "table-1"}

    async def fake_delete(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        assert path == "/tables/table-1/rows/row-1"
        return None

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)
    monkeypatch.setattr(TracecatClient, "delete", fake_delete, raising=True)

    await core_table.delete_row(table="my_table", row_id="row-1")


@pytest.mark.anyio
async def test_download_table_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(self: TracecatClient, path: str, *, params=None):  # type: ignore[no-untyped-def]
        if path == "/tables/by-name/my_table":
            return {"id": "table-1"}
        assert path == "/tables/table-1/download"
        assert params == {"limit": 2}
        return [{"a": 1}, {"a": 2}]

    monkeypatch.setattr(TracecatClient, "get", fake_get, raising=True)

    rows = await core_table.download(name="my_table", format=None, limit=2)
    assert rows == [{"a": 1}, {"a": 2}]

    json_str = await core_table.download(name="my_table", format="json", limit=2)
    assert isinstance(json_str, str) and '"a":1' in json_str


@pytest.mark.anyio
async def test_download_table_validates_limit() -> None:
    with pytest.raises(ValueError, match="Cannot return more than 1000 rows"):
        await core_table.download(name="t", limit=1001)
