from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from tracecat.agent.channels.management_router import (
    start_slack_oauth,
    update_channel_token,
)
from tracecat.agent.channels.schemas import (
    AgentChannelTokenUpdate,
    SlackOAuthStartRequest,
)
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError


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
async def test_update_channel_token_returns_404_for_archived_preset() -> None:
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
