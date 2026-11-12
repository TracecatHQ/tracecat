"""The channel bot admin API: admin discovery, error mapping, OAuth redirects."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests._route_utils import iter_effective_api_routes
from tracecat import config
from tracecat.agent.channel_bots import provider as provider_module
from tracecat.agent.channel_bots import router as router_module
from tracecat.agent.channel_bots.schemas import (
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    ChannelBotInstallResult,
    ChannelBotOAuthCallback,
    ChannelBotOAuthResult,
    ChannelBotProvider,
    ChannelBotRunState,
    ChannelBotStatus,
    SlackBotDetails,
    SlackBotInstall,
    oauth_callback_path,
)
from tracecat.api import extensions
from tracecat.api.app import app, create_app, entitlement_exception_handler
from tracecat.api.lifespan import StoppableTaskFactory
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
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

APP_URL = "https://app.example.com"
ROLE = Role(
    type="user",
    service_id="tracecat-api",
    user_id=uuid.uuid4(),
    workspace_id=uuid.uuid4(),
    organization_id=uuid.uuid4(),
    scopes=frozenset({"agent:read", "agent:update"}),
)
SESSION = object()
INSTALL_BODY = {
    "provider": "slack",
    "agent_preset_id": str(uuid.uuid4()),
    "client_id": "1.2",
    "client_secret": "secret",
    "signing_secret": "signing",
    "return_url": f"{APP_URL}/agents/1",
}


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


def with_extensions(monkeypatch: pytest.MonkeyPatch, **named: object) -> None:
    entries = [
        SimpleNamespace(name=name, load=Mock(return_value=lambda ext=ext: ext))
        for name, ext in named.items()
    ]
    monkeypatch.setattr(extensions, "entry_points", Mock(return_value=entries))


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    admin = AsyncMock()
    with_extensions(monkeypatch, other=PlainExtension(), channels=AdminExtension(admin))
    return admin


@pytest.fixture
def client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(router_module.router)
    test_app.add_exception_handler(EntitlementRequired, entitlement_exception_handler)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    test_app.dependency_overrides.update(
        {
            role_dependency: lambda: ROLE,
            get_async_session: lambda: SESSION,
            router_module._require_agent_channels_entitlement: lambda: None,
        }
    )
    return TestClient(test_app)


def bot_read() -> AgentChannelBotRead:
    now = datetime.now(UTC)
    return AgentChannelBotRead(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
        provider=ChannelBotProvider.SLACK,
        status=ChannelBotStatus.ACTIVE,
        provider_tenant_id="T1",
        provider_app_id="A1",
        endpoint_url="https://api.example.com/agent/channels/slack/x",
        created_at=now,
        updated_at=now,
        details=SlackBotDetails(
            provider=ChannelBotProvider.SLACK,
            provider_enterprise_id=None,
            provider_bot_user_id="UBOT",
            granted_scopes=["chat:write"],
            missing_scopes=[],
            update_needed=False,
            manifest_version=1,
            client_id="1.2",
            has_bot_token=True,
            has_signing_secret=True,
            has_client_secret=True,
            oauth_redirect_url="https://api.example.com/agent/channel-bots/slack/oauth/callback",
        ),
    )


def test_without_an_admin_the_routes_require_enterprise(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_extensions(monkeypatch, other=PlainExtension())

    response = client.get("/agent/channel-bots")

    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "EntitlementRequired"
    assert "require Tracecat Enterprise" in body["message"]


def test_routes_hand_the_request_to_the_installed_admin(
    client: TestClient, admin: AsyncMock
) -> None:
    expected = bot_read()
    admin.update_bot.return_value = expected

    response = client.patch(
        f"/agent/channel-bots/{expected.id}", json={"status": "paused"}
    )

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    admin.update_bot.assert_awaited_once_with(
        SESSION,
        ROLE,
        expected.id,
        AgentChannelBotUpdate(status=ChannelBotRunState.PAUSED),
    )


def test_install_hands_the_parsed_provider_request_to_the_admin(
    client: TestClient, admin: AsyncMock
) -> None:
    expected = ChannelBotInstallResult(
        bot=bot_read(), authorization_url="https://slack.com/oauth/v2/authorize?x=1"
    )
    admin.install.return_value = expected

    response = client.post("/agent/channel-bots/install", json=INSTALL_BODY)

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    admin.install.assert_awaited_once_with(
        SESSION, ROLE, SlackBotInstall.model_validate(INSTALL_BODY)
    )


@pytest.mark.parametrize(
    ("provider", "error_type"),
    [(None, "union_tag_not_found"), ("not-a-provider", "union_tag_invalid")],
)
def test_install_requires_a_known_provider(
    client: TestClient, admin: AsyncMock, provider: str | None, error_type: str
) -> None:
    body = {k: v for k, v in INSTALL_BODY.items() if k != "provider"}
    if provider is not None:
        body["provider"] = provider

    response = client.post("/agent/channel-bots/install", json=body)

    assert response.status_code == 422
    assert [e["type"] for e in response.json()["detail"]] == [error_type]
    assert admin.mock_calls == []


def test_list_hands_the_page_to_the_admin(client: TestClient, admin: AsyncMock) -> None:
    expected = CursorPaginatedResponse(items=[bot_read()], next_cursor="next")
    admin.list_bots.return_value = expected

    response = client.get("/agent/channel-bots", params={"limit": 5, "cursor": "prev"})

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    admin.list_bots.assert_awaited_once_with(
        SESSION,
        ROLE,
        agent_preset_id=None,
        provider=None,
        page=PageParams(limit=5, cursor="prev"),
    )


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(TracecatNotFoundError("gone"), 404), (TracecatValidationError("no"), 400)],
)
def test_admin_errors_map_to_http_errors(
    client: TestClient, admin: AsyncMock, error: Exception, status_code: int
) -> None:
    admin.rotate_endpoint.side_effect = error

    response = client.post(f"/agent/channel-bots/{uuid.uuid4()}/rotate")

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}


def test_a_bad_cursor_keeps_its_error_code(
    client: TestClient, admin: AsyncMock
) -> None:
    admin.list_bots.side_effect = PaginationError(
        "stale", code=PaginationErrorCode.CURSOR_MISMATCH
    )

    response = client.get("/agent/channel-bots", params={"cursor": "x"})

    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "cursor_mismatch"}}


@pytest.mark.parametrize(
    ("method", "path", "body", "field"),
    [
        pytest.param(
            "POST",
            "",
            {"agent_preset_id": str(uuid.uuid4()), "provider": "slack", "x": 1},
            "x",
            id="create",
        ),
        pytest.param(
            "PATCH", f"/{uuid.uuid4()}", {"status": "paused", "x": 1}, "x", id="update"
        ),
        pytest.param(
            "PATCH",
            f"/{uuid.uuid4()}",
            {"credentials": {"provider": "slack", "bot_token": "xoxb-1"}},
            "bot_token",
            id="credentials",
        ),
        pytest.param("POST", "/install", {**INSTALL_BODY, "x": 1}, "x", id="install"),
    ],
)
def test_request_models_reject_unknown_fields(
    client: TestClient,
    admin: AsyncMock,
    method: str,
    path: str,
    body: dict[str, object],
    field: str,
) -> None:
    response = client.request(method, f"/agent/channel-bots{path}", json=body)

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert [(e["type"], e["loc"][-1]) for e in errors] == [("extra_forbidden", field)]
    assert admin.mock_calls == []


def location_query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(location).query)


def get_callback(query_string: str, provider: str = "slack") -> httpx.Response:
    return TestClient(app, raise_server_exceptions=False).get(
        f"/agent/channel-bots/{provider}/oauth/callback?{query_string}",
        follow_redirects=False,
    )


def test_callback_redirects_to_the_return_url_on_the_app_host(
    admin: AsyncMock,
) -> None:
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=f"{APP_URL}/agents/1?tab=channels", ok=True
    )

    response = get_callback("state=s&code=c")

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith(f"{APP_URL}/agents/1?")
    assert location_query(location) == {
        "tab": ["channels"],
        "channel_bot_install": ["success"],
    }
    admin.complete_oauth.assert_awaited_once_with(
        ChannelBotProvider.SLACK, ChannelBotOAuthCallback(state="s", code="c")
    )


def test_callback_passes_provider_specific_parameters_to_the_admin(
    admin: AsyncMock,
) -> None:
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=f"{APP_URL}/agents/1", ok=True
    )

    response = get_callback(
        "state=s&code=c&tenant=t1&x=a&x=b&return_url=https%3A%2F%2Fevil.example.net"
    )

    assert response.status_code == 307
    assert response.headers["location"].startswith(f"{APP_URL}/agents/1?")
    provider, callback = admin.complete_oauth.await_args.args
    assert provider is ChannelBotProvider.SLACK
    assert (callback.state, callback.code) == ("s", "c")
    assert callback.model_extra == {
        "tenant": "t1",
        "x": ["a", "b"],
        "return_url": "https://evil.example.net",
    }


def test_callback_without_a_state_is_refused_without_a_redirect(
    admin: AsyncMock,
) -> None:
    response = get_callback("code=c")

    assert response.status_code == 422
    assert "location" not in response.headers
    admin.complete_oauth.assert_not_awaited()


@pytest.mark.parametrize(
    "return_url",
    [
        None,
        "https://evil.example.net/x",
        "javascript:alert(1)",
        "javascript://app.example.com/x",
        "http://[x",
        "https:///evil.example.net",
        "https:evil.example.net",
    ],
)
def test_callback_never_redirects_off_the_app_origin(
    admin: AsyncMock, return_url: str | None
) -> None:
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=return_url, ok=False, message="Invalid OAuth state"
    )

    response = get_callback("state=s")

    location = response.headers["location"]
    assert location.startswith(f"{APP_URL}?")
    assert location_query(location) == {
        "channel_bot_install": ["error"],
        "channel_bot_message": ["Invalid OAuth state"],
    }


@pytest.mark.parametrize(
    ("app_url", "return_url"),
    [
        ("https://app.example.com", "http://app.example.com/agents/1"),
        ("http://app.example.com", "https://app.example.com/agents/1"),
    ],
)
def test_callback_return_url_may_use_either_web_scheme(
    admin: AsyncMock, monkeypatch: pytest.MonkeyPatch, app_url: str, return_url: str
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", app_url)
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=return_url, ok=True
    )

    response = get_callback("state=s")

    assert response.status_code == 307
    assert response.headers["location"] == f"{return_url}?channel_bot_install=success"


@pytest.mark.parametrize(
    "return_url", ["https:///evil.example.net/x", "https:evil.example.net", "/x"]
)
def test_a_blank_app_url_accepts_no_return_url(
    admin: AsyncMock, monkeypatch: pytest.MonkeyPatch, return_url: str
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "")
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=return_url, ok=True
    )

    response = get_callback("state=s")

    assert response.status_code == 307
    assert response.headers["location"] == "?channel_bot_install=success"


def test_callback_without_an_admin_redirects_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    with_extensions(monkeypatch, other=PlainExtension())

    response = get_callback("state=s&code=c")

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith(f"{APP_URL}?")
    assert location_query(location) == {
        "channel_bot_install": ["error"],
        "channel_bot_message": ["Channel bots require Tracecat Enterprise"],
    }


def test_a_failing_admin_redirects_with_a_generic_error(admin: AsyncMock) -> None:
    admin.complete_oauth.side_effect = RuntimeError("boom")

    response = get_callback("state=s&code=c")

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith(f"{APP_URL}?")
    assert location_query(location) == {
        "channel_bot_install": ["error"],
        "channel_bot_message": ["OAuth callback failed"],
    }


def test_two_admins_fail_api_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    with_extensions(monkeypatch, a=AdminExtension(object()), b=AdminExtension(object()))
    with pytest.raises(RuntimeError, match="a, b"):
        create_app()


def test_admin_routes_are_entitlement_gated_and_the_callback_is_public() -> None:
    paths = {
        (route.path, method)
        for route in iter_effective_api_routes(app)
        for method in route.methods
        if "/agent/channel-bots" in route.path
    }
    assert ("/agent/channel-bots/{provider}/oauth/callback", "GET") in paths
    assert ("/workspaces/{workspace_id}/agent/channel-bots", "GET") in paths
    assert ("/workspaces/{workspace_id}/agent/channel-bots/install", "POST") in paths
    assert ("/workspaces/{workspace_id}/agent/channel-bots/{bot_id}", "PATCH") in paths
    gate = router_module._require_agent_channels_entitlement
    assert any(dep.dependency is gate for dep in router_module.router.dependencies)
    assert not router_module.public_router.dependencies


def test_the_callback_stays_out_of_the_openapi_schema() -> None:
    assert not any(
        path.startswith("/agent/channel-bots/") for path in app.openapi()["paths"]
    )


def test_slack_oauth_callback_path_is_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    with_extensions(monkeypatch)
    path = oauth_callback_path(ChannelBotProvider.SLACK)
    assert path == "/agent/channel-bots/slack/oauth/callback"

    response = TestClient(app).get(f"{path}?state=s", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(f"{APP_URL}?")


@pytest.mark.parametrize(
    ("provider", "status_code"),
    [
        ("not-a-provider", 422),
        ("https:%2F%2Fevil.example.net", 404),
        ("%2F%2Fevil.example.net", 404),
    ],
)
def test_an_unknown_callback_provider_is_refused_without_a_redirect(
    admin: AsyncMock, provider: str, status_code: int
) -> None:
    response = get_callback("state=s&code=c", provider=provider)

    assert response.status_code == status_code
    assert "location" not in response.headers
    admin.complete_oauth.assert_not_awaited()
