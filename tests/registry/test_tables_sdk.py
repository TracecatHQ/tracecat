"""Tests for the Tables SDK client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.sdk.tables import TablesClient


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    """Create a mock TracecatClient."""
    client = MagicMock()
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

    mock_tracecat_client.post.assert_called_once_with(
        "/tables/test_table/search",
        json={},
    )
    assert result["items"] == [{"id": "row-1"}]
    assert result["next_cursor"] == "abc"
    assert result["prev_cursor"] is None
    assert result["has_more"] is True
    assert result["has_previous"] is False
    assert result["total_estimate"] == 7


@pytest.mark.anyio
async def test_search_rows_rejects_unexpected_dict_shape(
    tables_client: TablesClient, mock_tracecat_client: MagicMock
) -> None:
    """Unexpected dictionary payloads still raise a ValueError."""
    mock_tracecat_client.post.return_value = {"foo": "bar"}

    with pytest.raises(ValueError, match="Unexpected search response"):
        await tables_client.search_rows(table="test_table")
