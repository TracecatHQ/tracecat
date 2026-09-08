"""Tests for the Tables SDK client."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.tables import TablesClient
from tracecat_registry.types import TableSearchResponse


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("table_name", "encoded_name"),
    [
        ("sample_rows", "sample_rows"),
        ("legacy-name", "legacy-name"),
        ("legacy name", "legacy%20name"),
        ("café", "caf%C3%A9"),
        ("表格", "%E8%A1%A8%E6%A0%BC"),
    ],
)
async def test_aggregate_rows_preserves_json_and_internal_gateway_path(
    monkeypatch: pytest.MonkeyPatch,
    table_name: str,
    encoded_name: str,
) -> None:
    """Exercise the real SDK URL construction and wire serialization."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "groups": [{"amount": "9007199254740992.1", "count": 2}],
                "truncated": True,
            },
        )

    def transport(*, uds: str) -> httpx.MockTransport:
        assert uds == "/tmp/aggregate-test.sock"
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    client = TracecatClient(
        action_gateway_socket="/tmp/aggregate-test.sock",
        workspace_id="test-workspace",
    )
    # Plain JSON is intentional: the server owns the recursive query shape.
    spec = {
        "group_by": [],
        "filters": {"and": [{"field": "amount", "op": "in", "value": []}]},
        "aggs": None,
        "limit": 1,
        "sort": None,
    }
    result = await client.tables.aggregate_rows(table_name, spec)

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == (
        f"http://tracecat-action-gateway/internal/tables/{encoded_name}/aggregate"
    )
    assert orjson.loads(requests[0].content) == spec
    assert result == {
        "groups": [{"amount": "9007199254740992.1", "count": 2}],
        "truncated": True,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "table_name",
    [
        "../workflows/00000000-0000-4000-8000-000000000001/publish#",
        "%2e%2e%2fworkflows%2fpublish%23",
        "sample_rows?redirect=/workflows",
        "sample_rows#fragment",
        "sample_rows/../other",
        "sample_rows\\other",
        "sample_rows\n",
        ".",
        "..",
        "sample_rows\x7f",
        "",
    ],
)
async def test_aggregate_rows_rejects_url_syntax_before_request(
    table_name: str,
    tables_client: TablesClient,
    mock_tracecat_client: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="Table name must"):
        await tables_client.aggregate_rows(table_name, {"group_by": []})
    mock_tracecat_client.post.assert_not_awaited()


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    """Create a mock TracecatClient."""
    client = MagicMock()
    client.delete = AsyncMock()
    client.patch = AsyncMock()
    client.post = AsyncMock()
    return client


@pytest.fixture
def tables_client(mock_tracecat_client: MagicMock) -> TablesClient:
    """Create a TablesClient with mocked HTTP client."""
    return TablesClient(mock_tracecat_client)


@pytest.mark.anyio
async def test_search_rows_normalizes_legacy_rows_payload(
    tables_client: TablesClient, mock_tracecat_client: MagicMock
) -> None:
    """Legacy paginated payloads that use rows should be normalized."""
    mock_tracecat_client.post.return_value = {
        "rows": [{"id": "row-1"}],
        "next_cursor": "abc",
        "prev_cursor": None,
        "has_more": True,
        "has_previous": False,
        "total_estimate": 7,
    }

    result = await tables_client.search_rows(table="test_table")
    assert isinstance(result, dict)
    paginated_result = cast(TableSearchResponse, result)

    mock_tracecat_client.post.assert_called_once_with(
        "/tables/test_table/search",
        json={},
    )
    assert paginated_result["items"] == [{"id": "row-1"}]
    assert paginated_result["next_cursor"] == "abc"
    assert paginated_result["prev_cursor"] is None
    assert paginated_result["has_more"] is True
    assert paginated_result["has_previous"] is False
    assert paginated_result.get("total_estimate") == 7


@pytest.mark.anyio
async def test_table_schema_helpers_use_internal_table_paths(
    tables_client: TablesClient, mock_tracecat_client: MagicMock
) -> None:
    """Schema editing helpers call the expected internal table endpoints."""
    column = {"name": "score", "type": "NUMERIC"}
    update = {"nullable": False}

    await tables_client.update_table(name="indicators", new_name="indicators_v2")
    await tables_client.create_column(table="indicators_v2", column=column)
    await tables_client.update_column(
        table="indicators_v2",
        column="score",
        update=update,
    )
    await tables_client.delete_column(table="indicators_v2", column="score")

    mock_tracecat_client.patch.assert_any_await(
        "/tables/indicators",
        json={"name": "indicators_v2"},
    )
    mock_tracecat_client.post.assert_any_await(
        "/tables/indicators_v2/columns",
        json=column,
    )
    mock_tracecat_client.patch.assert_any_await(
        "/tables/indicators_v2/columns/score",
        json=update,
    )
    mock_tracecat_client.delete.assert_awaited_once_with(
        "/tables/indicators_v2/columns/score"
    )


@pytest.mark.anyio
async def test_search_rows_rejects_unexpected_dict_shape(
    tables_client: TablesClient, mock_tracecat_client: MagicMock
) -> None:
    """Unexpected dictionary payloads still raise a ValueError."""
    mock_tracecat_client.post.return_value = {"foo": "bar"}

    with pytest.raises(ValueError, match="Unexpected search response"):
        await tables_client.search_rows(table="test_table")
