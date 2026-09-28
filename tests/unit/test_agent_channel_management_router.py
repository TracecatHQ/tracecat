from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from tracecat.agent.channels.management_router import (
    start_slack_oauth,
    update_channel_token,
)
from tracecat.agent.channels.router import handle_slack_oauth_callback
from tracecat.agent.channels.schemas import (
    AgentChannelTokenUpdate,
    SlackChannelTokenConfig,
    SlackOAuthStartRequest,
)
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.anyio
async def test_start_slack_oauth_rejects_mismatched_token_preset() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    params = SlackOAuthStartRequest(
        token_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
        client_id="client-id",
        client_secret="client-secret",
        signing_secret="signing-secret",
        return_url="https://app.tracecat.com/settings",
    )
    existing_token = SimpleNamespace(
        id=params.token_id,
        agent_preset_id=uuid.uuid4(),
    )
    mock_service = AsyncMock()
    mock_service.get_token.return_value = existing_token

    with patch(
        "tracecat.agent.channels.management_router.AgentChannelService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            oauth_start_fn = getattr(
                start_slack_oauth, "__wrapped__", start_slack_oauth
            )
            await oauth_start_fn(
                params=params,
                role=role,
                session=AsyncMock(),
            )

    assert exc_info.value.status_code == 400
    assert "does not match the requested agent preset" in exc_info.value.detail


@pytest.mark.anyio
async def test_start_slack_oauth_returns_404_for_missing_token() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    params = SlackOAuthStartRequest(
        token_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
        client_id="client-id",
        client_secret="client-secret",
        signing_secret="signing-secret",
        return_url="https://app.tracecat.com/settings",
    )
    mock_service = AsyncMock()
    mock_service.get_token.return_value = None

    with patch(
        "tracecat.agent.channels.management_router.AgentChannelService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            oauth_start_fn = getattr(
                start_slack_oauth, "__wrapped__", start_slack_oauth
            )
            await oauth_start_fn(
                params=params,
                role=role,
                session=AsyncMock(),
            )

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_start_slack_oauth_returns_404_for_soft_deleted_existing_token() -> None:
    """Slack OAuth updates reject tokens whose preset is soft-deleted."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    params = SlackOAuthStartRequest(
        token_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
        client_id="client-id",
        client_secret="client-secret",
        signing_secret="signing-secret",
        return_url="https://app.tracecat.com/settings",
    )
    existing_token = SimpleNamespace(
        id=params.token_id,
        agent_preset_id=params.agent_preset_id,
        channel_type="slack",
        config={},
    )
    mock_service = MagicMock()
    mock_service.get_token = AsyncMock(return_value=existing_token)
    mock_service.parse_stored_channel_config.return_value = SlackChannelTokenConfig(
        slack_bot_token="__tracecat_pending_bot_token__",
        slack_client_id="old-client-id",
        slack_client_secret="old-client-secret",
        slack_signing_secret="old-signing-secret",
    )
    mock_service.update_token = AsyncMock(
        side_effect=TracecatNotFoundError(
            "Agent preset with ID 'deleted-preset' not found in workspace"
        )
    )

    with patch(
        "tracecat.agent.channels.management_router.AgentChannelService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            oauth_start_fn = getattr(
                start_slack_oauth, "__wrapped__", start_slack_oauth
            )
            await oauth_start_fn(
                params=params,
                role=role,
                session=AsyncMock(),
            )

    assert exc_info.value.status_code == 404
    assert "not found in workspace" in exc_info.value.detail


@pytest.mark.anyio
async def test_slack_oauth_callback_rejects_soft_deleted_preset_before_exchange() -> (
    None
):
    """Slack OAuth callbacks stop before token exchange for soft-deleted presets."""
    token_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    return_url = "https://app.tracecat.com/settings"
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    token = SimpleNamespace(
        id=token_id,
        workspace_id=workspace_id,
        agent_preset_id=preset_id,
        channel_type="slack",
        config={},
    )
    service_instances = []

    class FakeAgentChannelService:
        @classmethod
        def parse_slack_oauth_state(cls, state: str) -> dict[str, str]:
            assert state == "state"
            return {
                "token_id": str(token_id),
                "workspace_id": str(workspace_id),
                "return_url": return_url,
            }

        def __init__(self, session, role):
            self.session = session
            self.role = role
            self.exchange_slack_oauth_code = AsyncMock(return_value="xoxb-new-token")
            self.update_token = AsyncMock()
            service_instances.append(self)

        async def get_token(self, requested_token_id: uuid.UUID):
            assert requested_token_id == token_id
            return token

        async def require_active_preset_for_token(self, token_arg, *, lock: bool):
            assert token_arg is token
            assert lock is True
            raise TracecatNotFoundError(
                f"Agent preset with ID '{preset_id}' not found in workspace"
            )

        def parse_stored_channel_config(self, *args, **kwargs):
            raise AssertionError(
                "Slack config should not be parsed before preset check"
            )

    with (
        patch(
            "tracecat.agent.channels.router.AgentChannelService",
            FakeAgentChannelService,
        ),
        patch(
            "tracecat.agent.channels.router.get_async_session_context_manager",
            return_value=_AsyncContext(AsyncMock()),
        ),
        patch(
            "tracecat.agent.channels.router._resolve_service_role_for_workspace",
            AsyncMock(return_value=role),
        ),
        patch(
            "tracecat.agent.channels.router.config.TRACECAT__PUBLIC_APP_URL",
            "https://app.tracecat.com",
        ),
    ):
        response = await handle_slack_oauth_callback(
            code="auth-code",
            state="state",
            error=None,
            error_description=None,
        )

    assert len(service_instances) == 1
    service_instances[0].exchange_slack_oauth_code.assert_not_awaited()
    service_instances[0].update_token.assert_not_awaited()
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    assert "slack_connect=error" in location
    assert query["slack_message"] == ["Channel configuration is no longer active"]
    assert str(preset_id) not in location


@pytest.mark.anyio
async def test_update_channel_token_returns_404_for_soft_deleted_preset() -> None:
    """Channel token updates surface soft-deleted presets as not found."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    token_id = uuid.uuid4()
    existing_token = SimpleNamespace(
        id=token_id,
        agent_preset_id=uuid.uuid4(),
    )
    mock_service = AsyncMock()
    mock_service.get_token.return_value = existing_token
    mock_service.update_token.side_effect = TracecatNotFoundError(
        "Agent preset not found in workspace"
    )

    with patch(
        "tracecat.agent.channels.management_router.AgentChannelService",
        return_value=mock_service,
    ):
        with pytest.raises(HTTPException) as exc_info:
            update_token_fn = getattr(
                update_channel_token, "__wrapped__", update_channel_token
            )
            await update_token_fn(
                token_id=token_id,
                params=AgentChannelTokenUpdate(is_active=True),
                role=role,
                session=AsyncMock(),
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Agent preset not found in workspace"
