"""HTTP errors for inaccessible operations and invalid diff cursors."""

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import get_args
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from temporalio.service import RPCError, RPCStatusCode

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat import config
from tracecat.api.app import scope_denied_exception_handler
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.schemas import ScopeDeniedResponse
from tracecat.db.engine import get_async_session
from tracecat.exceptions import ScopeDeniedError
from tracecat.pagination import PageParams
from tracecat.workspace_sync.operations import router, storage
from tracecat.workspace_sync.operations.lifecycle_client import SyncLifecycleService
from tracecat.workspace_sync.operations.schemas import (
    SyncOperationCreate,
    SyncOperationRead,
)
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationStartError


@pytest.fixture(autouse=True)
def mock_workflow_start(monkeypatch):
    """Keep HTTP unit tests isolated from live Temporal queues."""
    handle = Mock(
        query=AsyncMock(
            side_effect=RPCError(
                "Missing synthetic operation", RPCStatusCode.NOT_FOUND, b""
            )
        )
    )
    client = Mock(
        start_workflow=AsyncMock(), get_workflow_handle_for=Mock(return_value=handle)
    )
    monkeypatch.setattr(
        "tracecat.workspace_sync.operations.lifecycle_client.get_temporal_client",
        AsyncMock(return_value=client),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["preview", "apply", "retry"])
async def test_api_waits_for_temporal_and_repeats_same_command(
    session, svc_role, monkeypatch, phase
):
    inputs = push_inputs()
    now = datetime.now(UTC)
    response = SyncOperationRead(
        id=inputs.id,
        direction="push",
        status="queued",
        stage="fetching",
        created_at=now,
        expires_at=now + timedelta(hours=24),
        inputs=inputs,
    )
    requests = []

    async def request(_service, *args):
        requests.append(args)
        if len(requests) == 1:
            raise SyncOperationStartError(
                "Unable to confirm Git sync startup. Retry the same operation."
            )
        return response

    monkeypatch.setattr(
        SyncLifecycleService, "create" if phase == "preview" else "command", request
    )
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    url = "/workflows/sync/operations"
    payload = inputs.model_dump(mode="json")
    if phase != "preview":
        url += f"/{inputs.id}/{phase}"
        payload = {
            "command_id": str(uuid.uuid4()),
            "preview_revision": 1,
            "state_revision": 3,
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        failed = await client.post(url, json=payload)
        assert failed.status_code == 503
        assert failed.json() == {
            "detail": "Unable to confirm Git sync startup. Retry the same operation."
        }
        accepted = await client.post(url, json=payload)
        assert accepted.status_code == 202
        assert accepted.json()["id"] == str(inputs.id)
    assert requests[0] == requests[1]
    assert (
        await SyncOperationService(session, svc_role).list(PageParams())
    ).items == []


@pytest.mark.anyio
async def test_api_bounds_connection_startup(session, svc_role, monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(router.asyncio, "timeout", lambda _: real_timeout(0.01))

    async def unavailable(_service, _inputs):
        await asyncio.Event().wait()

    monkeypatch.setattr(SyncLifecycleService, "create", unavailable)
    log = Mock()
    monkeypatch.setattr(router.logger, "warning", log)
    with pytest.raises(router.HTTPException) as failure:
        await router.create_sync_operation(
            role=svc_role, session=session, params=push_inputs()
        )
    assert failure.value.status_code == 503
    assert failure.value.__context__ is None
    assert log.call_args.kwargs["error_type"] == "TimeoutError"


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
            method,
            f"/workflows/sync/operations/{operation_id}{suffix}",
            json={
                "command_id": str(uuid.uuid4()),
                "preview_revision": 0,
                "state_revision": 0,
            }
            if method == "POST"
            else None,
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


@pytest.mark.anyio
@pytest.mark.parametrize("cursor,status", [("invalid!", 400), ("a" * 8193, 422)])
async def test_operation_cursor_is_http_client_error(session, svc_role, cursor, status):
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/workflows/sync/operations", params={"cursor": cursor}
        )
    assert response.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_durable_pull_requires_explicit_confirmation_not_legacy_dry_run(
    session, svc_role, dry_run
):
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/workflows/sync/operations",
            json={
                "id": str(uuid.uuid4()),
                "direction": "pull",
                "pull": {"commit_sha": "a" * 40, "dry_run": dry_run},
            },
        )
    page = await SyncOperationService(session, svc_role).list(PageParams())
    if dry_run:
        assert response.status_code == 400
        assert page.items == []
    else:
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert response.json()["result"] is None
        # The workflow's first activity creates the history copy later.
        assert page.items == []


@pytest.mark.anyio
async def test_legacy_sync_scope_rejects_push_before_persistence_but_allows_pull(
    session, svc_role
):
    role = svc_role.model_copy(update={"scopes": frozenset({"workspace_sync:pull"})})
    service = SyncOperationService(session, role)
    app = FastAPI()
    app.include_router(router.router)
    app.add_exception_handler(ScopeDeniedError, scope_denied_exception_handler)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/workflows/sync/operations", json=push_inputs().model_dump(mode="json")
        )
        assert response.status_code == 403
        denial = ScopeDeniedResponse.model_validate(response.json())
        assert denial.error.code == "insufficient_scope"
        assert denial.error.required_scopes == ["workspace_sync:push"]
        assert denial.error.missing_scopes == ["workspace_sync:push"]
        assert (await service.list(PageParams())).items == []
        inputs = SyncOperationCreate.model_validate(
            {
                "id": str(uuid.uuid4()),
                "direction": "pull",
                "pull": {"commit_sha": "a" * 40},
            }
        )
        response = await client.post(
            "/workflows/sync/operations", json=inputs.model_dump(mode="json")
        )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
    assert (await service.list(PageParams())).items == []
    schema = app.openapi()
    forbidden = schema["paths"]["/workflows/sync/operations"]["post"]["responses"][
        "403"
    ]
    assert forbidden["content"]["application/json"]["schema"]["anyOf"] == [
        {"$ref": "#/components/schemas/ScopeDeniedResponse"},
        {"$ref": "#/components/schemas/SyncOperationError"},
    ]


@pytest.mark.anyio
async def test_history_route_uses_canonical_pagination_limits(session, svc_role):
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        allowed = await client.get(
            "/workflows/sync/operations",
            params={"limit": config.TRACECAT__LIMIT_CURSOR_MAX},
        )
        rejected = await client.get(
            "/workflows/sync/operations",
            params={"limit": config.TRACECAT__LIMIT_CURSOR_MAX + 1},
        )
    assert allowed.status_code == 200
    assert rejected.status_code == 422
