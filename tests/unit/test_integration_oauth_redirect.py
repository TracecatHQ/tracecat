"""Tests for integration OAuth callback redirect targets."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from tracecat import config
from tracecat.auth.types import Role
from tracecat.integrations import router as router_module
from tracecat.integrations.providers.github.mcp import GitHubMCPProvider
from tracecat.integrations.providers.github.oauth import GitHubOAuthProvider
from tracecat.integrations.router import _oauth_callback_redirect_url, oauth_callback


def test_oauth_callback_redirect_url_uses_integrations_for_oauth_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://tracecat.test")
    workspace_id = uuid.uuid4()

    redirect_url = _oauth_callback_redirect_url(
        provider_impl=GitHubOAuthProvider,
        workspace_id=workspace_id,
    )

    assert (
        redirect_url == f"https://tracecat.test/workspaces/{workspace_id}/integrations"
    )


def test_oauth_callback_redirect_url_uses_mcp_servers_for_mcp_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://tracecat.test")
    workspace_id = uuid.uuid4()

    redirect_url = _oauth_callback_redirect_url(
        provider_impl=GitHubMCPProvider,
        workspace_id=workspace_id,
    )

    assert (
        redirect_url == f"https://tracecat.test/workspaces/{workspace_id}/mcp-servers"
    )


@pytest.mark.anyio
async def test_oauth_callback_resolves_scopes_for_state_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-level grants must apply once the callback binds the workspace."""
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    oauth_state = MagicMock()
    oauth_state.user_id = user_id
    oauth_state.workspace_id = workspace_id
    oauth_state.provider_id = "google_drive"
    oauth_state.code_verifier = None
    oauth_state.expires_at = datetime.now(UTC) + timedelta(minutes=5)

    session = AsyncMock()
    session.get.return_value = oauth_state

    async def fake_compute_effective_scopes(role: Role) -> frozenset[str]:
        if role.workspace_id == workspace_id:
            return frozenset({"integration:create", "integration:update"})
        return frozenset()

    monkeypatch.setattr(
        router_module, "compute_effective_scopes", fake_compute_effective_scopes
    )
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.OFF)

    captured_roles: list[Role] = []

    class FakeIntegrationService:
        def __init__(self, session: object, *, role: Role) -> None:
            captured_roles.append(role)

        def _is_custom_mcp_oauth_provider(self, provider_id: str) -> bool:
            return False

        async def resolve_provider_impl(self, *, provider_key: object) -> None:
            return None

    monkeypatch.setattr(router_module, "IntegrationService", FakeIntegrationService)

    unscoped_role = Role(
        type="user",
        user_id=user_id,
        organization_id=org_id,
        workspace_id=None,
        service_id="tracecat-api",
        scopes=frozenset(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await oauth_callback(
            session=session,
            role=unscoped_role,
            code="code",
            state=str(uuid.uuid4()),
        )

    assert exc_info.value.detail == "Provider not found"
    assert len(captured_roles) == 1
    bound_role = captured_roles[0]
    assert bound_role.workspace_id == workspace_id
    assert bound_role.scopes == frozenset({"integration:create", "integration:update"})
