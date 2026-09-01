"""HTTP coverage for Inbox endpoints."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.inbox.router as inbox_router_module
from tracecat.auth.types import Role
from tracecat.pagination import CursorPaginatedResponse


@pytest.mark.anyio
async def test_list_items_forwards_optional_case_filter(
    client: TestClient,
    test_role: Role,
) -> None:
    """The router parses case_id as a UUID and preserves the unfiltered path."""
    assert test_role.workspace_id is not None
    case_id = uuid.uuid4()
    provider = SimpleNamespace(
        list_items=AsyncMock(
            return_value=CursorPaginatedResponse(
                items=[],
                next_cursor=None,
                prev_cursor=None,
                has_more=False,
                has_previous=False,
                total_estimate=None,
            )
        )
    )
    path = f"/workspaces/{test_role.workspace_id}/inbox/items"

    with patch.object(
        inbox_router_module,
        "get_inbox_provider",
        return_value=provider,
    ):
        filtered = client.get(path, params={"case_id": str(case_id)})
        unfiltered = client.get(path)

    assert filtered.status_code == status.HTTP_200_OK
    assert unfiltered.status_code == status.HTTP_200_OK
    calls = provider.list_items.await_args_list
    assert calls[0].kwargs["case_id"] == case_id
    assert calls[1].kwargs["case_id"] is None
