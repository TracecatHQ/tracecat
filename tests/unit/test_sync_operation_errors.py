"""HTTP errors for inaccessible operations and invalid diff cursors."""

import asyncio
import base64
import uuid
from typing import get_args
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.api.app import scope_denied_exception_handler
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.schemas import ScopeDeniedResponse
from tracecat.db.engine import get_async_session
from tracecat.exceptions import ScopeDeniedError
from tracecat.pagination import PageParams
from tracecat.workspace_sync.operations import router, storage
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import SyncOperationStartError


@pytest.fixture(autouse=True)
def mock_workflow_start(monkeypatch):
    """Keep HTTP unit tests isolated from live Temporal queues."""
    monkeypatch.setattr(SyncOperationService, "start_workflow", AsyncMock())


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["preview", "apply", "retry"])
async def test_api_waits_for_start_and_retries_same_operation(
    session, svc_role, monkeypatch, phase
):
    """An ambiguous start must return 503 and retain the same phase/attempt."""
    inputs = push_inputs()
    service = SyncOperationService(session, svc_role)
    if phase != "preview":
        operation = await service.create(inputs)
        operation.status = "ready" if phase == "apply" else "failed"
        await session.commit()

    starts: list[tuple[uuid.UUID, str, int]] = []

    async def start(_service, operation):
        starts.append((operation.id, operation.status, operation.attempt))
        # The recorded request is committed before network startup begins.
        assert not _service.session.in_transaction()
        if len(starts) == 1:
            raise SyncOperationStartError("Synthetic start failure")

    monkeypatch.setattr(SyncOperationService, "start_workflow", start)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_async_session] = lambda: session
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: svc_role
    url = "/workflows/sync/operations"
    payload = inputs.model_dump(mode="json")
    if phase != "preview":
        url += f"/{inputs.id}/{'apply' if phase == 'apply' else 'retry'}"
        payload = None
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
    assert starts[0] == starts[1]
    assert starts[0][2] == (1 if phase == "retry" else 0)


@pytest.mark.anyio
async def test_api_bounds_connection_startup(session, svc_role, monkeypatch):
    """A slow initial connection cannot leave the API waiting indefinitely."""
    real_timeout = asyncio.timeout
    monkeypatch.setattr(router.asyncio, "timeout", lambda _: real_timeout(0.01))

    async def unavailable(_service, _operation):
        await asyncio.Event().wait()

    monkeypatch.setattr(SyncOperationService, "start_workflow", unavailable)
    with pytest.raises(router.HTTPException) as failure:
        await router.create_sync_operation(
            role=svc_role, session=session, params=push_inputs()
        )
    assert failure.value.status_code == 503
    assert failure.value.__context__ is None


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
        assert len(page.items) == 1


@pytest.mark.anyio
async def test_legacy_sync_scope_rejects_push_before_persistence_but_allows_pull(
    session, svc_role
):
    role = svc_role.model_copy(update={"scopes": frozenset({"workflow:sync"})})
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
        assert denial.error.required_scopes == ["workspace_sync:sync"]
        assert denial.error.missing_scopes == ["workspace_sync:sync"]
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
    assert len((await service.list(PageParams())).items) == 1
    schema = app.openapi()
    forbidden = schema["paths"]["/workflows/sync/operations"]["post"]["responses"][
        "403"
    ]
    assert forbidden["content"]["application/json"]["schema"]["anyOf"] == [
        {"$ref": "#/components/schemas/ScopeDeniedResponse"},
        {"$ref": "#/components/schemas/SyncOperationError"},
    ]
