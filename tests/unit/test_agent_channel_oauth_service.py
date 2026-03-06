from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from tracecat.agent.channels import service as channel_service_module
from tracecat.agent.channels.service import AgentChannelService


def test_build_slack_oauth_authorization_url_includes_redirect_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        channel_service_module.config,
        "TRACECAT__PUBLIC_API_URL",
        "https://api.example.com",
    )

    authorization_url = AgentChannelService.build_slack_oauth_authorization_url(
        client_id="client-id",
        state="state-token",
    )

    parsed = urlparse(authorization_url)
    query = parse_qs(parsed.query)

    assert query["redirect_uri"] == [
        "https://api.example.com/agent/channels/slack/oauth/callback"
    ]
    assert query["scope"] == [
        "app_mentions:read,channels:history,chat:write,chat:write.customize,groups:history,im:history,mpim:history,reactions:read,reactions:write,users:read,users:read.email"
    ]


@pytest.mark.anyio
async def test_exchange_slack_oauth_code_passes_redirect_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        channel_service_module.config,
        "TRACECAT__PUBLIC_API_URL",
        "https://api.example.com",
    )

    fake_client = AsyncMock()
    fake_client.oauth_v2_access = AsyncMock(return_value={"access_token": "xoxb-test"})
    monkeypatch.setattr(
        channel_service_module,
        "AsyncWebClient",
        lambda: fake_client,
    )

    access_token = await AgentChannelService.exchange_slack_oauth_code(
        client_id="client-id",
        client_secret="client-secret",
        code="oauth-code",
    )

    assert access_token == "xoxb-test"
    await_args = fake_client.oauth_v2_access.await_args
    assert await_args is not None
    assert (
        await_args.kwargs["redirect_uri"]
        == "https://api.example.com/agent/channels/slack/oauth/callback"
    )
