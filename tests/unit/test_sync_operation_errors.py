"""HTTP errors for inaccessible operations and invalid diff cursors."""

import base64
import uuid
from typing import get_args
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.db.engine import get_async_session
from tracecat.workspace_sync.operations import router, storage
from tracecat.workspace_sync.operations.service import SyncOperationService


@pytest.mark.anyio
@pytest.mark.parametrize("foreign_actor", [False, True])
@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", ""),
        ("POST", "/apply"),
        ("POST", "/retry"),
        ("GET", "/diffs"),
        ("GET", "/diffs/0"),
    ],
)
async def test_inaccessible_operation_is_http_not_found(
    session, svc_role, foreign_actor, method, suffix
):
    operation_id = uuid.uuid4()
    if foreign_actor:
        service = SyncOperationService(session, svc_role)
        operation = await service.create(push_inputs())
        operation.actor_id = uuid.uuid4()
        await session.commit()
        operation_id = operation.id
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(
            method, f"/workflows/sync/operations/{operation_id}{suffix}"
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "Sync operation not found"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "count,offset,valid",
    [
        (0, 0, True),
        (1, 0, True),
        (1, 50, False),
        (50, 50, False),
        (51, 50, True),
        (51, 100, False),
    ],
)
async def test_diff_cursor_bounds_before_storage(monkeypatch, count, offset, valid):
    download = AsyncMock(return_value=b'{"items":[]}')
    monkeypatch.setattr(storage, "_download", download)
    cursor = base64.urlsafe_b64encode(str(offset).encode()).decode()
    if valid:
        await storage.read_diff_page("synthetic", cursor, diff_count=count)
        download.assert_awaited_once_with(f"synthetic.page-{offset}")
    else:
        with pytest.raises(ValueError):
            await storage.read_diff_page("synthetic", cursor, diff_count=count)
        download.assert_not_awaited()


@pytest.mark.anyio
async def test_out_of_range_diff_cursor_is_http_bad_request(
    session, svc_role, monkeypatch
):
    operation = await SyncOperationService(session, svc_role).create(push_inputs())
    operation.artifact_key = "synthetic"
    operation.summary = {"diff_count": 1}
    await session.commit()
    download = AsyncMock()
    monkeypatch.setattr(storage, "_download", download)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/workflows/sync/operations/{operation.id}/diffs",
            params={"cursor": "NTA="},
        )
    assert response.status_code == 400
    download.assert_not_awaited()
