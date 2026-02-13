from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from tracecat.auth import credentials
from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole, WorkspaceRole


@pytest.mark.anyio
async def test_compute_effective_scopes_uses_user_rbac_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    expected_scopes = frozenset({"workflow:read", "table:update"})

    cached_scopes = AsyncMock(return_value=expected_scopes)
    monkeypatch.setattr(credentials, "_compute_effective_scopes_cached", cached_scopes)

    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert scopes == expected_scopes
    cached_scopes.assert_awaited_once_with(user_id, organization_id, workspace_id)


@pytest.mark.anyio
async def test_compute_effective_scopes_uses_service_allowlist_path() -> None:
    role = Role(
        type="service",
        service_id="tracecat-schedule-runner",
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert "workflow:execute" in scopes
    assert "case:create" in scopes
    assert "table:update" in scopes
    assert "variable:read" in scopes
    assert "secret:read" in scopes
    assert "agent:execute" in scopes
    assert "schedule:update" in scopes
    assert "tag:delete" in scopes
    assert "integration:read" in scopes
    assert "integration:create" in scopes
    assert "integration:update" in scopes
    assert "integration:delete" in scopes
    assert "workspace:read" in scopes
    assert "workspace:member:read" in scopes
    assert "action:*:execute" in scopes


@pytest.mark.anyio
async def test_compute_effective_scopes_unknown_service_principal_gets_empty_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials, "SERVICE_PRINCIPAL_SCOPES", {})

    role = Role(
        type="service",
        service_id="tracecat-executor",
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert scopes == frozenset()


@pytest.mark.anyio
async def test_compute_effective_scopes_falls_back_to_legacy_memberships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    cached_scopes = AsyncMock(return_value=frozenset())
    has_assignments = AsyncMock(return_value=False)
    monkeypatch.setattr(credentials, "_compute_effective_scopes_cached", cached_scopes)
    monkeypatch.setattr(
        credentials, "_has_any_rbac_assignments_cached", has_assignments
    )

    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        org_role=OrgRole.ADMIN,
        workspace_role=WorkspaceRole.EDITOR,
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert "org:settings:update" in scopes
    assert "org:settings:delete" in scopes
    assert "org:registry:update" in scopes
    assert "workflow:execute" in scopes
    assert "table:update" in scopes
    assert "action:core.*:execute" in scopes
    cached_scopes.assert_awaited_once_with(user_id, organization_id, workspace_id)
    has_assignments.assert_awaited_once_with(user_id, organization_id, workspace_id)


@pytest.mark.anyio
async def test_compute_effective_scopes_skips_legacy_fallback_with_rbac_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    cached_scopes = AsyncMock(return_value=frozenset())
    has_assignments = AsyncMock(return_value=True)
    monkeypatch.setattr(credentials, "_compute_effective_scopes_cached", cached_scopes)
    monkeypatch.setattr(
        credentials, "_has_any_rbac_assignments_cached", has_assignments
    )

    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=user_id,
        organization_id=organization_id,
        org_role=OrgRole.OWNER,
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert scopes == frozenset()
    cached_scopes.assert_awaited_once_with(user_id, organization_id, None)
    has_assignments.assert_awaited_once_with(user_id, organization_id, None)
