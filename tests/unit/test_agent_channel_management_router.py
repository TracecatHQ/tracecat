from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from tracecat.agent.channels.management_router import start_slack_oauth
from tracecat.agent.channels.schemas import SlackOAuthStartRequest
from tracecat.auth.types import Role


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
