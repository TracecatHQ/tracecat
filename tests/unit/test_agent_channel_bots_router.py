"""The channel bot admin socket: provider discovery, error mapping, OAuth redirects."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, HTTPException

from tests._route_utils import iter_effective_api_routes
from tracecat import config
from tracecat.agent.channel_bots import provider as provider_module
from tracecat.agent.channel_bots import router as router_module
from tracecat.agent.channel_bots.schemas import (
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    ChannelBotProvider,
    ChannelBotRunState,
    ChannelBotStatus,
    SlackBotOAuthCallback,
    SlackBotOAuthOutcome,
)
from tracecat.api import extensions
from tracecat.api.app import app
from tracecat.api.lifespan import StoppableTaskFactory
from tracecat.auth.types import Role
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import (
    CursorPaginatedResponse,
    PageParams,
    PaginationError,
    PaginationErrorCode,
)


@pytest.fixture(autouse=True)
def clean_caches() -> Iterator[None]:
    extensions.load_api_extensions.cache_clear()
    provider_module.get_channel_bot_admin.cache_clear()
    yield
    extensions.load_api_extensions.cache_clear()
    provider_module.get_channel_bot_admin.cache_clear()


class PlainExtension:
    def routers(self) -> Sequence[APIRouter]:
        return ()

    def lifespan_tasks(self) -> Sequence[StoppableTaskFactory]:
        return ()


class AdminExtension(PlainExtension):
    def __init__(self, admin: object) -> None:
        self.admin = admin

    def channel_bot_admin(self) -> Any:
        return self.admin


def install(monkeypatch: pytest.MonkeyPatch, **named: object) -> None:
    entries = [
        SimpleNamespace(name=name, load=Mock(return_value=lambda ext=ext: ext))
        for name, ext in named.items()
    ]
    monkeypatch.setattr(extensions, "entry_points", Mock(return_value=entries))


def role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read", "agent:update"}),
    )


def bot_read() -> AgentChannelBotRead:
    now = datetime.now(UTC)
    return AgentChannelBotRead(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
        provider=ChannelBotProvider.SLACK,
        status=ChannelBotStatus.ACTIVE,
        provider_tenant_id="T1",
        provider_enterprise_id=None,
        provider_app_id="A1",
        provider_bot_user_id="UBOT",
        granted_scopes=["chat:write"],
        missing_scopes=[],
        update_needed=False,
        manifest_version=1,
        client_id="1.2",
        has_bot_token=True,
        has_signing_secret=True,
        has_client_secret=True,
        endpoint_url="https://api.example.com/agent/channels/slack/x",
        oauth_redirect_url="https://api.example.com/agent/channel-bots/slack/oauth/callback",
        created_at=now,
        updated_at=now,
    )


def unwrap(fn: Any) -> Any:
    return getattr(fn, "__wrapped__", fn)


def test_no_provider_installed_requires_enterprise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, other=PlainExtension())
    assert provider_module.get_channel_bot_admin() is None
    with pytest.raises(EntitlementRequired, match="require Tracecat Enterprise"):
        router_module._require_provider()


def test_the_extension_that_implements_the_admin_is_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = object()
    install(monkeypatch, other=PlainExtension(), channels=AdminExtension(admin))
    assert router_module._require_provider() is admin


def test_two_providers_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, a=AdminExtension(object()), b=AdminExtension(object()))
    with pytest.raises(RuntimeError, match="a, b"):
        provider_module.get_channel_bot_admin()


@pytest.mark.anyio
async def test_routes_hand_the_request_to_the_provider() -> None:
    expected = bot_read()
    admin = AsyncMock()
    admin.update_bot.return_value = expected
    params = AgentChannelBotUpdate(status=ChannelBotRunState.PAUSED)
    session, r = AsyncMock(), role()

    result = await unwrap(router_module.update_bot)(
        bot_id=expected.id, params=params, role=r, session=session, admin=admin
    )

    assert result is expected
    admin.update_bot.assert_awaited_once_with(session, r, expected.id, params)


@pytest.mark.anyio
async def test_list_hands_the_page_to_the_provider() -> None:
    expected = CursorPaginatedResponse(items=[bot_read()], next_cursor="next")
    admin = AsyncMock()
    admin.list_bots.return_value = expected
    session, r = AsyncMock(), role()

    result = await unwrap(router_module.list_bots)(
        role=r,
        session=session,
        admin=admin,
        agent_preset_id=None,
        provider=None,
        limit=5,
        cursor="prev",
    )

    assert result is expected
    admin.list_bots.assert_awaited_once_with(
        session,
        r,
        agent_preset_id=None,
        provider=None,
        page=PageParams(limit=5, cursor="prev"),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status_code"),
    [(TracecatNotFoundError("gone"), 404), (TracecatValidationError("no"), 400)],
)
async def test_provider_errors_map_to_http_errors(
    error: Exception, status_code: int
) -> None:
    admin = AsyncMock()
    admin.rotate_endpoint.side_effect = error
    with pytest.raises(HTTPException) as exc:
        await unwrap(router_module.rotate_bot_endpoint)(
            bot_id=uuid.uuid4(), role=role(), session=AsyncMock(), admin=admin
        )
    assert exc.value.status_code == status_code
    assert exc.value.detail == str(error)


@pytest.mark.anyio
async def test_a_bad_cursor_keeps_its_error_code() -> None:
    admin = AsyncMock()
    admin.list_bots.side_effect = PaginationError(
        "stale", code=PaginationErrorCode.CURSOR_MISMATCH
    )
    with pytest.raises(HTTPException) as exc:
        await unwrap(router_module.list_bots)(
            role=role(),
            session=AsyncMock(),
            admin=admin,
            agent_preset_id=None,
            provider=None,
            limit=5,
            cursor="x",
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == {"code": "cursor_mismatch"}


def query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(location).query)


@pytest.mark.anyio
async def test_callback_redirects_to_the_return_url_on_the_app_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com")
    admin = AsyncMock()
    admin.complete_slack_oauth.return_value = SlackBotOAuthOutcome(
        return_url="https://app.example.com/agents/1?tab=channels", ok=True
    )
    install(monkeypatch, channels=AdminExtension(admin))

    response = await router_module.handle_slack_bot_oauth_callback(
        state="s", code="c", error=None, error_description=None
    )

    location = response.headers["location"]
    assert location.startswith("https://app.example.com/agents/1?")
    assert query(location) == {"tab": ["channels"], "slack_connect": ["success"]}
    admin.complete_slack_oauth.assert_awaited_once_with(
        SlackBotOAuthCallback(state="s", code="c")
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "return_url",
    [None, "https://evil.example.net/x", "javascript:alert(1)", "http://[x"],
)
async def test_callback_never_redirects_off_the_app_host(
    monkeypatch: pytest.MonkeyPatch, return_url: str | None
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com")
    admin = AsyncMock()
    admin.complete_slack_oauth.return_value = SlackBotOAuthOutcome(
        return_url=return_url, ok=False, message="Invalid OAuth state"
    )
    install(monkeypatch, channels=AdminExtension(admin))

    response = await router_module.handle_slack_bot_oauth_callback(
        state="s", code=None, error=None, error_description=None
    )

    location = response.headers["location"]
    assert location.startswith("https://app.example.com?")
    assert query(location) == {
        "slack_connect": ["error"],
        "slack_message": ["Invalid OAuth state"],
    }


@pytest.mark.anyio
async def test_callback_without_a_provider_or_with_a_failing_one_redirects_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com")
    install(monkeypatch)
    response = await router_module.handle_slack_bot_oauth_callback(
        state="s", code="c", error=None, error_description=None
    )
    assert query(response.headers["location"])["slack_connect"] == ["error"]

    provider_module.get_channel_bot_admin.cache_clear()
    extensions.load_api_extensions.cache_clear()
    admin = AsyncMock()
    admin.complete_slack_oauth.side_effect = RuntimeError("boom")
    install(monkeypatch, channels=AdminExtension(admin))
    response = await router_module.handle_slack_bot_oauth_callback(
        state="s", code="c", error=None, error_description=None
    )
    assert query(response.headers["location"]) == {
        "slack_connect": ["error"],
        "slack_message": ["Slack OAuth callback failed"],
    }

    provider_module.get_channel_bot_admin.cache_clear()
    extensions.load_api_extensions.cache_clear()
    install(monkeypatch, a=AdminExtension(admin), b=AdminExtension(admin))
    response = await router_module.handle_slack_bot_oauth_callback(
        state="s", code="c", error=None, error_description=None
    )
    assert query(response.headers["location"])["slack_connect"] == ["error"]


def test_admin_routes_are_entitlement_gated_and_the_callback_is_public() -> None:
    paths = {
        (route.path, method)
        for route in iter_effective_api_routes(app)
        for method in route.methods
        if "/agent/channel-bots" in route.path
    }
    assert ("/agent/channel-bots/slack/oauth/callback", "GET") in paths
    assert ("/workspaces/{workspace_id}/agent/channel-bots", "GET") in paths
    assert ("/workspaces/{workspace_id}/agent/channel-bots/{bot_id}", "PATCH") in paths
    gate = router_module._require_agent_channels_entitlement
    assert any(dep.dependency is gate for dep in router_module.router.dependencies)
    assert not router_module.public_router.dependencies
