"""Regressions for queued preview recovery and effective authorization."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.auth.types import Role
from tracecat.db.locks import pg_advisory_connection_lock, pg_advisory_xact_lock
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.workspace_sync.operations import auth
from tracecat.workspace_sync.operations.auth import refresh_sync_role
from tracecat.workspace_sync.operations.router import router
from tracecat.workspace_sync.operations.service import SyncOperationService


@pytest.mark.anyio
@pytest.mark.parametrize("actor_type", ["service_account", "user"])
@pytest.mark.parametrize(
    ("captured", "live", "expected"),
    [
        ({"workflow:read"}, {"workflow:update"}, {"workflow:read"}),
        ({"workflow:update"}, {"workflow:read"}, {"workflow:read"}),
        ({"workflow:*"}, {"workflow:read"}, {"workflow:read"}),
        ({"workflow:read"}, {"workflow:*"}, {"workflow:read"}),
        ({"workflow:read"}, {"variable:read"}, set()),
        ({"workflow:read"}, {"workflow:read", "variable:update"}, {"workflow:read"}),
        (set(), {"*"}, set()),
    ],
)
async def test_actor_effective_scope_intersection(
    captured, live, expected, actor_type, monkeypatch
):
    role = Role(
        type=actor_type,
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        service_account_id=uuid.uuid4() if actor_type == "service_account" else None,
        user_id=uuid.uuid4() if actor_type == "user" else None,
        scopes=frozenset(captured),
    )
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(
        disabled_at=None,
        workspace_id=role.workspace_id,
        scopes=[SimpleNamespace(name=name) for name in live],
    )
    session.get.return_value = SimpleNamespace(is_active=True, is_superuser=False)
    monkeypatch.setattr(
        auth, "query_effective_scopes", AsyncMock(return_value=frozenset(live))
    )
    monkeypatch.setattr(
        auth, "workspace_membership_exists", AsyncMock(return_value=True)
    )
    session.scalars.return_value = [
        "workflow:read",
        "workflow:update",
        "workflow:*",
        "variable:read",
        "variable:update",
    ]
    refreshed = await refresh_sync_role(session, role)
    assert refreshed.scopes == frozenset(expected)
    assert not refreshed.is_platform_superuser


@pytest.mark.anyio
@pytest.mark.parametrize("revoked", ["user", "organization", "workspace"])
async def test_user_scope_refresh_rejects_revoked_access(revoked, monkeypatch):
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset({"workflow:read"}),
    )
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(
        is_active=revoked != "user", is_superuser=False
    )
    session.scalar.return_value = None if revoked == "organization" else role.user_id
    monkeypatch.setattr(
        auth,
        "query_effective_scopes",
        AsyncMock(return_value=frozenset({"workflow:read"})),
    )
    monkeypatch.setattr(
        auth,
        "workspace_membership_exists",
        AsyncMock(return_value=revoked != "workspace"),
    )
    with pytest.raises(TracecatAuthorizationError):
        await refresh_sync_role(session, role)


@pytest.mark.anyio
async def test_unprepared_preview_remains_retryable_after_queue_outage(
    session, svc_role
):
    service = SyncOperationService(session, svc_role)
    row = await service.create(push_inputs())
    row.status = "failed"
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()
    assert service.read(row).can_retry
    assert (await service.retry(row.id)).status == "queued"


@pytest.mark.anyio
@pytest.mark.parametrize("key", [-(2**63) - 1, 2**63])
async def test_invalid_lock_keys_do_not_touch_connections(key):
    connection = AsyncMock()
    with pytest.raises(ValueError):
        async with pg_advisory_connection_lock(connection, key):
            pytest.fail("Invalid lock key entered the context")
    connection.execute.assert_not_awaited()
    connection.invalidate.assert_not_awaited()
    with pytest.raises(ValueError):
        await pg_advisory_xact_lock(connection, key)
    connection.execute.assert_not_awaited()


def test_operation_openapi_declares_expected_http_failures():
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    prefix = "/workflows/sync/operations"
    for suffix in ("", "/{operation_id}/apply", "/{operation_id}/retry"):
        assert "409" in paths[prefix + suffix]["post"]["responses"]
    for code in ("400", "410"):
        assert code in paths[prefix + "/{operation_id}/diffs"]["get"]["responses"]
    for code in ("404", "410"):
        assert (
            code in paths[prefix + "/{operation_id}/diffs/{index}"]["get"]["responses"]
        )
