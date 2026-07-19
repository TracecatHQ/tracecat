from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from tracecat.auth import credentials
from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.authz.scopes import (
    PRESET_ROLE_SCOPES,
    SERVICE_PRINCIPAL_SCOPES,
    backfill_legacy_role_scopes,
)


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
    assert "workflow:terminate" in scopes
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
async def test_compute_effective_scopes_service_with_user_id_uses_allowlist() -> None:
    role = Role(
        type="service",
        service_id="tracecat-schedule-runner",
        user_id=uuid.uuid4(),
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert "workflow:execute" in scopes
    assert "action:*:execute" in scopes


@pytest.mark.anyio
async def test_compute_effective_scopes_preserves_explicit_service_scopes() -> None:
    expected_scopes = frozenset({"case:read", "action:core.cases.list_cases:execute"})
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        user_id=uuid.uuid4(),
        scopes=expected_scopes,
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert scopes == expected_scopes


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
async def test_compute_effective_scopes_returns_empty_when_no_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no RBAC assignments, user gets empty scopes (no legacy fallback)."""
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    cached_scopes = AsyncMock(return_value=frozenset())
    monkeypatch.setattr(credentials, "_compute_effective_scopes_cached", cached_scopes)

    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=user_id,
        organization_id=organization_id,
    )

    scopes = await credentials.compute_effective_scopes(role)

    assert scopes == frozenset()
    cached_scopes.assert_awaited_once_with(user_id, organization_id, None)


@pytest.mark.parametrize("scopes", [None, frozenset()], ids=["none", "empty"])
def test_backfill_legacy_user_scopes_for_none_and_empty(
    scopes: frozenset[str] | None,
) -> None:
    """Both documented pre-migration encodings backfill from legacy role fields."""
    # ``workspace_role`` is a preserved legacy extra (Role uses extra="allow"),
    # so build via a mapping rather than a typed kwarg.
    role = Role.model_validate(
        {
            "type": "user",
            "service_id": "tracecat-api",
            "user_id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "workspace_id": uuid.uuid4(),
            "scopes": scopes,
            "workspace_role": WorkspaceRole.EDITOR,
        }
    )

    backfilled = backfill_legacy_role_scopes(role)

    assert backfilled.scopes == PRESET_ROLE_SCOPES["workspace-editor"]


def test_backfill_fresh_empty_user_without_legacy_fields_stays_empty() -> None:
    """A current, intentionally-scopeless user is not broadened (anti-escalation)."""
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset(),
    )

    backfilled = backfill_legacy_role_scopes(role)

    assert not backfilled.scopes


def test_backfill_legacy_empty_service_uses_service_principal_scopes() -> None:
    """A historical empty service role resolves to its service-principal scopes."""
    role = Role(
        type="service",
        service_id="tracecat-executor",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset(),
    )

    backfilled = backfill_legacy_role_scopes(role)

    assert backfilled.scopes == SERVICE_PRINCIPAL_SCOPES["tracecat-executor"]
