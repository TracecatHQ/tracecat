"""The channel bot admin API: admin discovery, error mapping, OAuth redirects."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, get_args
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
from tracecat.agent.channel_bots.provider import (
    ChannelListingNeedsReinstall,
    ChannelListingNotSupported,
)
from tracecat.agent.channel_bots.schemas import (
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    AgentChannelRouteRead,
    AgentChannelRouteSet,
    ChannelBotAppManifest,
    ChannelBotAppOwner,
    ChannelBotChannel,
    ChannelBotInstallResult,
    ChannelBotOAuthCallback,
    ChannelBotOAuthResult,
    ChannelBotPlatformApp,
    ChannelBotPlatformInstall,
    ChannelBotProvider,
    ChannelBotRunState,
    ChannelBotStatus,
    ChannelBotTransport,
    SlackBotDetails,
    SlackBotInstall,
    oauth_callback_path,
)
from tracecat.agent.channel_users.enums import ChannelAccessPolicy
from tracecat.api import extensions
from tracecat.api.app import (
    app,
    create_app,
    entitlement_exception_handler,
    scope_denied_exception_handler,
)
from tracecat.api.lifespan import StoppableTaskFactory
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.auth.users import optional_current_active_user
from tracecat.db.engine import get_async_session
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.pagination import (
    CursorPaginatedResponse,
    PageParams,
    PaginationError,
    PaginationErrorCode,
)
from tracecat.tiers.enums import Entitlement

if TYPE_CHECKING:
    from loguru import Record

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
PLATFORM_INSTALL_BODY = {
    "provider": "slack",
    "agent_preset_id": str(uuid.uuid4()),
    "return_url": f"{APP_URL}/agents/1",
}
ROUTE_BODY = {"provider_channel_id": "C1", "agent_preset_id": str(uuid.uuid4())}


@pytest.fixture(autouse=True)
def clean_caches() -> Iterator[None]:
    extensions.load_api_extensions.cache_clear()
    provider_module.get_channel_bot_admin.cache_clear()
    yield
    extensions.load_api_extensions.cache_clear()
    provider_module.get_channel_bot_admin.cache_clear()


@pytest.fixture(autouse=True)
def signed_out_browser() -> Iterator[None]:
    """The callback's browser has no Tracecat session unless a test signs one in."""
    app.dependency_overrides[optional_current_active_user] = lambda: None
    yield
    app.dependency_overrides.pop(optional_current_active_user, None)


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


def make_client(role: Role = ROLE, *, entitled: bool = True) -> TestClient:
    test_app = FastAPI()
    test_app.include_router(router_module.router)
    test_app.add_exception_handler(EntitlementRequired, entitlement_exception_handler)
    test_app.add_exception_handler(ScopeDeniedError, scope_denied_exception_handler)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    overrides: dict[Callable[..., Any], Callable[..., Any]] = {
        role_dependency: lambda: role,
        get_async_session: lambda: SESSION,
    }
    if entitled:
        overrides[router_module._require_agent_channels_preview] = lambda: None
    test_app.dependency_overrides.update(overrides)
    return TestClient(test_app)


@pytest.fixture
def client() -> TestClient:
    return make_client()


def grant(monkeypatch: pytest.MonkeyPatch, *granted: Entitlement) -> AsyncMock:
    async def check(_session: object, _role: Role, entitlement: Entitlement) -> None:
        if entitlement not in granted:
            raise EntitlementRequired(entitlement.value)

    check_mock = AsyncMock(side_effect=check)
    monkeypatch.setattr(router_module, "check_entitlement", check_mock)
    return check_mock


@pytest.fixture
def unentitled(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    return grant(monkeypatch)


def entitlement_required_body(entitlement: Entitlement) -> dict[str, object]:
    return {
        "type": "EntitlementRequired",
        "message": f"Feature '{entitlement.value}' requires an upgraded plan.",
        "detail": {"entitlement": entitlement.value},
    }


ENTITLEMENT_REQUIRED_BODY = entitlement_required_body(
    Entitlement.AGENT_CHANNELS_PREVIEW
)


def bot_read() -> AgentChannelBotRead:
    now = datetime.now(UTC)
    return AgentChannelBotRead(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
        provider=ChannelBotProvider.SLACK,
        app_owner=ChannelBotAppOwner.WORKSPACE,
        status=ChannelBotStatus.ACTIVE,
        provider_tenant_id="T1",
        provider_app_id="A1",
        endpoint_url="https://api.example.com/agent/channels/slack/x",
        channel_access_policy=ChannelAccessPolicy.LINKED_APPROVALS,
        created_at=now,
        updated_at=now,
        details=SlackBotDetails(
            provider=ChannelBotProvider.SLACK,
            transport=ChannelBotTransport.HTTP,
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


def test_an_unentitled_workspace_is_refused(
    admin: AsyncMock, unentitled: AsyncMock
) -> None:
    response = make_client(entitled=False).get("/agent/channel-bots")

    assert response.status_code == 403
    assert response.json() == ENTITLEMENT_REQUIRED_BODY
    unentitled.assert_awaited_once_with(
        SESSION, ROLE, Entitlement.AGENT_CHANNELS_PREVIEW
    )
    assert admin.mock_calls == []


def test_the_entitlement_is_checked_before_the_admin_is_looked_up(
    monkeypatch: pytest.MonkeyPatch, unentitled: AsyncMock
) -> None:
    with_extensions(monkeypatch, other=PlainExtension())

    response = make_client(entitled=False).get("/agent/channel-bots")

    assert response.status_code == 403
    assert response.json() == ENTITLEMENT_REQUIRED_BODY


WORKSPACE_ROUTES = [
    pytest.param("GET", "", None, "agent:read", id="list"),
    pytest.param(
        "POST",
        "",
        {"agent_preset_id": str(uuid.uuid4()), "provider": "slack"},
        "agent:update",
        id="create",
    ),
    pytest.param(
        "PATCH",
        f"/{uuid.uuid4()}",
        {"status": "paused"},
        "agent:update",
        id="update",
    ),
    pytest.param("POST", f"/{uuid.uuid4()}/rotate", None, "agent:update", id="rotate"),
    pytest.param(
        "POST", f"/{uuid.uuid4()}/disconnect", None, "agent:update", id="disconnect"
    ),
    pytest.param("POST", "/install", INSTALL_BODY, "agent:update", id="install"),
    pytest.param("GET", "/platform-apps", None, "agent:read", id="platform_apps"),
    pytest.param(
        "POST",
        "/platform-apps/install",
        PLATFORM_INSTALL_BODY,
        "agent:update",
        id="install_platform",
    ),
    pytest.param(
        "GET",
        f"/{uuid.uuid4()}/app-manifest?app_name=Triage",
        None,
        "agent:read",
        id="app_manifest",
    ),
    pytest.param("GET", f"/{uuid.uuid4()}/routes", None, "agent:read", id="routes"),
    pytest.param("GET", f"/{uuid.uuid4()}/channels", None, "agent:read", id="channels"),
    pytest.param(
        "GET",
        f"/{uuid.uuid4()}/routable-channels",
        None,
        "agent:read",
        id="routable_channels",
    ),
    pytest.param(
        "POST", f"/{uuid.uuid4()}/routes", ROUTE_BODY, "agent:update", id="add_route"
    ),
    pytest.param(
        "PUT", f"/{uuid.uuid4()}/routes", ROUTE_BODY, "agent:update", id="set_route"
    ),
    pytest.param(
        "DELETE",
        f"/{uuid.uuid4()}/routes?provider_channel_id=C1",
        None,
        "agent:update",
        id="delete_route",
    ),
]
ADMIN_METHODS = (
    "list_bots",
    "create_bot",
    "update_bot",
    "rotate_endpoint",
    "disconnect_bot",
    "install",
    "list_platform_apps",
    "install_platform",
    "app_manifest",
    "list_routes",
    "add_route",
    "list_channels",
    "list_routable_channels",
    "set_route",
    "delete_route",
)
CHANNELS = Entitlement.AGENT_CHANNELS
PREVIEW = Entitlement.AGENT_CHANNELS_PREVIEW


@pytest.mark.parametrize(("method", "path", "body", "scope"), WORKSPACE_ROUTES)
@pytest.mark.parametrize(
    ("granted", "checked", "served"),
    [
        pytest.param({CHANNELS}, [PREVIEW], False, id="agent_channels_only"),
        pytest.param({PREVIEW}, [PREVIEW], True, id="preview_only"),
        pytest.param({CHANNELS, PREVIEW}, [PREVIEW], True, id="both"),
    ],
)
def test_routes_require_only_the_preview(
    admin: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    granted: set[Entitlement],
    checked: list[Entitlement],
    served: bool,
    method: str,
    path: str,
    body: dict[str, object] | None,
    scope: str,
) -> None:
    check = grant(monkeypatch, *granted)
    admin.configure_mock(
        **{
            f"{name}.side_effect": TracecatNotFoundError("gone")
            for name in ADMIN_METHODS
        }
    )

    response = make_client(entitled=False).request(
        method, f"/agent/channel-bots{path}", json=body
    )

    assert [c.args[2] for c in check.await_args_list] == checked
    if served:
        assert response.status_code == 404
        assert response.json() == {"detail": "gone"}
        assert len(admin.mock_calls) == 1
    else:
        assert response.status_code == 403
        assert response.json() == entitlement_required_body(checked[-1])
        assert admin.mock_calls == []


@pytest.mark.parametrize(("method", "path", "body", "scope"), WORKSPACE_ROUTES)
def test_routes_require_their_scope(
    admin: AsyncMock,
    method: str,
    path: str,
    body: dict[str, object] | None,
    scope: str,
) -> None:
    # agent:update implies agent:read, so no denied role may hold it.
    role = ROLE.model_copy(update={"scopes": frozenset({"agent:read"} - {scope})})

    response = make_client(role).request(
        method, f"/agent/channel-bots{path}", json=body
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "insufficient_scope",
            "message": "You don't have permission to perform this action.",
            "required_scopes": [scope],
            "missing_scopes": [scope],
        }
    }
    assert admin.mock_calls == []


def test_routes_hand_the_request_to_the_installed_admin(
    client: TestClient, admin: AsyncMock
) -> None:
    expected = bot_read()
    admin.update_bot.return_value = expected

    response = client.patch(
        f"/agent/channel-bots/{expected.id}",
        json={"status": "paused", "channel_access_policy": "linked_only"},
    )

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    admin.update_bot.assert_awaited_once_with(
        SESSION,
        ROLE,
        expected.id,
        AgentChannelBotUpdate(
            status=ChannelBotRunState.PAUSED,
            channel_access_policy=ChannelAccessPolicy.LINKED_ONLY,
        ),
    )


@pytest.mark.parametrize(
    "body",
    [
        INSTALL_BODY,
        {**INSTALL_BODY, "app_token": "xapp-1"},
        {**INSTALL_BODY, "app_token": "xoxe.xapp-1", "app_refresh_token": "xoxe-1"},
    ],
    ids=["without_app_token", "with_app_token", "with_rotating_app_token"],
)
def test_install_hands_the_parsed_provider_request_to_the_admin(
    client: TestClient, admin: AsyncMock, body: dict[str, str]
) -> None:
    expected = ChannelBotInstallResult(
        bot=bot_read(), authorization_url="https://slack.com/oauth/v2/authorize?x=1"
    )
    admin.install.return_value = expected

    response = client.post("/agent/channel-bots/install", json=body)

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    admin.install.assert_awaited_once_with(
        SESSION, ROLE, SlackBotInstall.model_validate(body)
    )


@pytest.mark.parametrize(
    "app_token",
    ["", "xapp-", "xoxe.xapp-", "xoxe.xoxb-1", "xoxb-bot-token"],
    ids=[
        "blank",
        "bare_prefix",
        "bare_rotating_prefix",
        "rotating_bot_token",
        "bot_token",
    ],
)
def test_install_refuses_a_token_that_is_not_an_app_token(
    client: TestClient, admin: AsyncMock, app_token: str
) -> None:
    response = client.post(
        "/agent/channel-bots/install", json={**INSTALL_BODY, "app_token": app_token}
    )

    assert response.status_code == 422
    assert [error["loc"][-1] for error in response.json()["detail"]] == ["app_token"]
    assert admin.mock_calls == []


@pytest.mark.parametrize(
    "tokens",
    [
        {"app_token": "xoxe.xapp-1"},
        {"app_token": "xapp-1", "app_refresh_token": "xoxe-1"},
        {"app_refresh_token": "xoxe-1"},
    ],
    ids=["rotating_without_refresh", "refresh_with_non_rotating", "refresh_alone"],
)
def test_install_takes_a_refresh_token_with_a_rotating_app_token_only(
    client: TestClient, admin: AsyncMock, tokens: dict[str, str]
) -> None:
    response = client.post(
        "/agent/channel-bots/install", json={**INSTALL_BODY, **tokens}
    )

    assert response.status_code == 422
    assert "refresh token" in response.json()["detail"][0]["msg"]
    assert admin.mock_calls == []


def test_install_refuses_a_refresh_token_that_is_not_one(
    client: TestClient, admin: AsyncMock
) -> None:
    body = {**INSTALL_BODY, "app_token": "xoxe.xapp-1", "app_refresh_token": "xoxb-1"}

    response = client.post("/agent/channel-bots/install", json=body)

    assert response.status_code == 422
    assert [e["loc"][-1] for e in response.json()["detail"]] == ["app_refresh_token"]
    assert admin.mock_calls == []


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


def test_platform_routes_hand_the_request_to_the_admin(
    client: TestClient, admin: AsyncMock
) -> None:
    bot_id = uuid.uuid4()
    admin.list_platform_apps.return_value = [
        ChannelBotPlatformApp(provider=ChannelBotProvider.SLACK)
    ]
    installed = ChannelBotInstallResult(
        bot=bot_read(), authorization_url="https://slack.com/oauth/v2/authorize?x=1"
    )
    admin.install_platform.return_value = installed
    now = datetime.now(UTC)
    route = AgentChannelRouteRead(
        bot_id=bot_id,
        provider_channel_id="C1",
        agent_preset_id=uuid.UUID(ROUTE_BODY["agent_preset_id"]),
        created_at=now,
        updated_at=now,
    )
    admin.list_routes.return_value = CursorPaginatedResponse(items=[route])
    admin.add_route.return_value = route
    admin.set_route.return_value = route
    admin.delete_route.return_value = None

    apps = client.get("/agent/channel-bots/platform-apps")
    install = client.post(
        "/agent/channel-bots/platform-apps/install", json=PLATFORM_INSTALL_BODY
    )
    listed = client.get(f"/agent/channel-bots/{bot_id}/routes")
    added = client.post(f"/agent/channel-bots/{bot_id}/routes", json=ROUTE_BODY)
    put = client.put(f"/agent/channel-bots/{bot_id}/routes", json=ROUTE_BODY)
    deleted = client.delete(
        f"/agent/channel-bots/{bot_id}/routes", params={"provider_channel_id": "C1"}
    )

    assert apps.json() == [{"provider": "slack"}]
    assert install.json() == installed.model_dump(mode="json")
    assert listed.json()["items"] == [route.model_dump(mode="json")]
    assert added.status_code == 201
    assert added.json() == route.model_dump(mode="json")
    assert put.json() == route.model_dump(mode="json")
    assert deleted.status_code == 204
    admin.list_platform_apps.assert_awaited_once_with(SESSION, ROLE)
    admin.install_platform.assert_awaited_once_with(
        SESSION, ROLE, ChannelBotPlatformInstall.model_validate(PLATFORM_INSTALL_BODY)
    )
    admin.list_routes.assert_awaited_once_with(
        SESSION,
        ROLE,
        bot_id,
        agent_preset_id=None,
        page=PageParams(limit=20, cursor=None),
    )
    admin.add_route.assert_awaited_once_with(
        SESSION, ROLE, bot_id, AgentChannelRouteSet.model_validate(ROUTE_BODY)
    )
    admin.set_route.assert_awaited_once_with(
        SESSION, ROLE, bot_id, AgentChannelRouteSet.model_validate(ROUTE_BODY)
    )
    admin.delete_route.assert_awaited_once_with(SESSION, ROLE, bot_id, "C1")


@pytest.mark.parametrize(
    "manifest",
    [
        ChannelBotAppManifest(
            filename="slack-app-manifest.json",
            media_type="application/json",
            encoding="text",
            content='{"display_information": {"name": "SOC bot"}}',
            create_url="https://api.slack.com/apps?new_app=1&manifest_json=%7B%7D",
        ),
        # A provider with no create page ships a binary package instead.
        ChannelBotAppManifest(
            filename="app-package.zip",
            media_type="application/zip",
            encoding="base64",
            content="UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==",
            create_url=None,
        ),
    ],
    ids=["text", "binary"],
)
def test_a_bots_app_manifest_comes_from_the_admin_as_is(
    client: TestClient, admin: AsyncMock, manifest: ChannelBotAppManifest
) -> None:
    bot_id = uuid.uuid4()
    admin.app_manifest.return_value = manifest

    response = client.get(
        f"/agent/channel-bots/{bot_id}/app-manifest", params={"app_name": "SOC bot"}
    )

    assert response.status_code == 200
    assert response.json() == manifest.model_dump(mode="json")
    admin.app_manifest.assert_awaited_once_with(
        SESSION, ROLE, bot_id, app_name="SOC bot"
    )


@pytest.mark.parametrize(
    "params", [{}, {"app_name": ""}, {"app_name": "   "}, {"app_name": "x" * 256}]
)
def test_an_app_manifest_needs_a_name(
    client: TestClient, admin: AsyncMock, params: dict[str, str]
) -> None:
    response = client.get(
        f"/agent/channel-bots/{uuid.uuid4()}/app-manifest", params=params
    )

    assert response.status_code == 422
    admin.app_manifest.assert_not_awaited()


def test_a_platform_reconnect_can_replace_the_default_agent(
    client: TestClient, admin: AsyncMock
) -> None:
    admin.install_platform.return_value = ChannelBotInstallResult(
        bot=bot_read(), authorization_url="https://slack.com/oauth/v2/authorize"
    )
    body = {
        "provider": "slack",
        "bot_id": str(uuid.uuid4()),
        "agent_preset_id": str(uuid.uuid4()),
        "return_url": f"{APP_URL}/agents/1",
    }

    response = client.post("/agent/channel-bots/platform-apps/install", json=body)

    assert response.status_code == 200
    admin.install_platform.assert_awaited_once_with(
        SESSION, ROLE, ChannelBotPlatformInstall.model_validate(body)
    )


@pytest.mark.parametrize("channel", ["", " C1", "C 1", "C" * 256])
def test_a_route_needs_a_channel_id(
    client: TestClient, admin: AsyncMock, channel: str
) -> None:
    response = client.put(
        f"/agent/channel-bots/{uuid.uuid4()}/routes",
        json={**ROUTE_BODY, "provider_channel_id": channel},
    )

    assert response.status_code == 422
    assert admin.mock_calls == []


@pytest.mark.parametrize(
    "channel",
    [
        "19:LpxShHZZh9utjNcEmUS5aOEP9ASw85OUn05NcWYAhX81@thread.tacv2",
        "a/b?c#d%2F",
    ],
)
def test_route_delete_carries_any_channel_id_verbatim(
    client: TestClient, admin: AsyncMock, channel: str
) -> None:
    bot_id = uuid.uuid4()
    admin.delete_route.return_value = None

    response = client.delete(
        f"/agent/channel-bots/{bot_id}/routes", params={"provider_channel_id": channel}
    )

    assert response.status_code == 204
    admin.delete_route.assert_awaited_once_with(SESSION, ROLE, bot_id, channel)


@pytest.mark.parametrize("params", [{}, {"provider_channel_id": " C1"}])
def test_route_delete_needs_a_channel_id(
    client: TestClient, admin: AsyncMock, params: dict[str, str]
) -> None:
    response = client.delete(
        f"/agent/channel-bots/{uuid.uuid4()}/routes", params=params
    )

    assert response.status_code == 422
    assert admin.mock_calls == []


def test_list_hands_the_page_to_the_admin(client: TestClient, admin: AsyncMock) -> None:
    expected = CursorPaginatedResponse(items=[bot_read()], next_cursor="next")
    admin.list_bots.return_value = expected

    response = client.get(
        "/agent/channel-bots",
        params={"limit": 5, "cursor": "prev", "app_owner": "platform"},
    )

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    admin.list_bots.assert_awaited_once_with(
        SESSION,
        ROLE,
        agent_preset_id=None,
        provider=None,
        app_owner=ChannelBotAppOwner.PLATFORM,
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


def test_routes_list_hands_the_page_to_the_admin(
    client: TestClient, admin: AsyncMock
) -> None:
    bot_id = uuid.uuid4()
    admin.list_routes.return_value = CursorPaginatedResponse(
        items=[], next_cursor="next", has_more=True
    )

    preset_id = uuid.uuid4()

    response = client.get(
        f"/agent/channel-bots/{bot_id}/routes",
        params={"limit": 2, "cursor": "c", "agent_preset_id": str(preset_id)},
    )

    assert response.status_code == 200
    assert response.json()["next_cursor"] == "next"
    assert response.json()["has_more"] is True
    admin.list_routes.assert_awaited_once_with(
        SESSION,
        ROLE,
        bot_id,
        agent_preset_id=preset_id,
        page=PageParams(limit=2, cursor="c"),
    )


@pytest.mark.parametrize("limit", [0, 201])
def test_routes_list_refuses_a_limit_out_of_bounds(
    client: TestClient, admin: AsyncMock, limit: int
) -> None:
    response = client.get(
        f"/agent/channel-bots/{uuid.uuid4()}/routes", params={"limit": limit}
    )

    assert response.status_code == 422
    assert admin.mock_calls == []


def test_routes_list_keeps_a_bad_cursors_error_code(
    client: TestClient, admin: AsyncMock
) -> None:
    admin.list_routes.side_effect = PaginationError(
        "stale", code=PaginationErrorCode.CURSOR_MISMATCH
    )

    response = client.get(
        f"/agent/channel-bots/{uuid.uuid4()}/routes", params={"cursor": "x"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "cursor_mismatch"}}


def test_adding_a_route_for_a_routed_channel_is_a_conflict(
    client: TestClient, admin: AsyncMock
) -> None:
    admin.add_route.side_effect = TracecatConflictError("C1 already has a route")

    response = client.post(
        f"/agent/channel-bots/{uuid.uuid4()}/routes", json=ROUTE_BODY
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "C1 already has a route"}


# The bot's own channels for the agents list, and the tenant's for the routes editor.
CHANNEL_LISTINGS = [
    pytest.param("channels", "list_channels", id="channels"),
    pytest.param("routable-channels", "list_routable_channels", id="routable"),
]


@pytest.mark.parametrize(("path", "method"), CHANNEL_LISTINGS)
def test_channels_come_from_the_installed_admin(
    client: TestClient, admin: AsyncMock, path: str, method: str
) -> None:
    bot_id = uuid.uuid4()
    channels = [
        ChannelBotChannel(id="C1", name="alerts", is_private=False, is_member=True),
        ChannelBotChannel(id="G1", name="soc", is_private=True, is_member=False),
        ChannelBotChannel(
            id="19:a@thread.tacv2",
            name="General",
            is_private=False,
            is_member=True,
            team_id="19:a@thread.tacv2",
            team_name="SecOps",
        ),
    ]
    getattr(admin, method).return_value = CursorPaginatedResponse(
        items=channels, next_cursor="dGVhbTpD", has_more=True
    )

    response = client.get(
        f"/agent/channel-bots/{bot_id}/{path}",
        params={"limit": 2, "cursor": "dGVhbTpB"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [c.model_dump(mode="json") for c in channels]
    assert response.json()["next_cursor"] == "dGVhbTpD"
    assert response.json()["has_more"] is True
    getattr(admin, method).assert_awaited_once_with(
        SESSION, ROLE, bot_id, page=PageParams(limit=2, cursor="dGVhbTpB")
    )


@pytest.mark.parametrize("path", ["channels", "routable-channels"])
@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"limit": 0}, id="limit_too_small"),
        pytest.param({"limit": 201}, id="limit_too_large"),
        pytest.param({"cursor": "x" * 8193}, id="cursor_too_long"),
    ],
)
def test_channels_list_refuses_out_of_bounds_paging(
    client: TestClient, admin: AsyncMock, params: dict[str, object], path: str
) -> None:
    response = client.get(f"/agent/channel-bots/{uuid.uuid4()}/{path}", params=params)

    assert response.status_code == 422
    assert admin.mock_calls == []


@pytest.mark.parametrize(("path", "method"), CHANNEL_LISTINGS)
def test_channels_list_keeps_a_malformed_cursors_error_code(
    client: TestClient, admin: AsyncMock, path: str, method: str
) -> None:
    getattr(admin, method).side_effect = PaginationError(
        "bad cursor", code=PaginationErrorCode.INVALID_CURSOR
    )

    response = client.get(
        f"/agent/channel-bots/{uuid.uuid4()}/{path}", params={"cursor": "x"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "invalid_cursor"}}


@pytest.mark.parametrize(("path", "method"), CHANNEL_LISTINGS)
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        pytest.param(TracecatNotFoundError("gone"), 404, id="unknown_bot"),
        pytest.param(TracecatValidationError("paused"), 400, id="refused_state"),
        pytest.param(ChannelListingNotSupported("no list"), 501, id="not_supported"),
        pytest.param(ChannelListingNeedsReinstall("rescope"), 424, id="reinstall"),
    ],
)
def test_channel_listing_errors_map_to_http_errors(
    client: TestClient,
    admin: AsyncMock,
    error: Exception,
    status_code: int,
    path: str,
    method: str,
) -> None:
    getattr(admin, method).side_effect = error

    response = client.get(f"/agent/channel-bots/{uuid.uuid4()}/{path}")

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
        pytest.param(
            "POST",
            "/platform-apps/install",
            {**PLATFORM_INSTALL_BODY, "client_id": "1.2"},
            "client_id",
            id="install_platform",
        ),
        pytest.param(
            "PUT", f"/{uuid.uuid4()}/routes", {**ROUTE_BODY, "x": 1}, "x", id="route"
        ),
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
        ChannelBotProvider.SLACK,
        ChannelBotOAuthCallback(state="s", code="c"),
        user_id=None,
    )


def test_callback_keeps_the_error_code_the_admin_put_in_the_return_url(
    admin: AsyncMock,
) -> None:
    # The UI recognizes a claimed provider workspace by this code, not the message.
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=f"{APP_URL}/agents/channels?channel_bot_error=workspace_claimed",
        ok=False,
        message="Reworded refusal",
    )

    response = get_callback("state=s&code=c")

    assert location_query(response.headers["location"]) == {
        "channel_bot_error": ["workspace_claimed"],
        "channel_bot_install": ["error"],
        "channel_bot_message": ["Reworded refusal"],
    }


def test_callback_passes_the_browsers_signed_in_user(admin: AsyncMock) -> None:
    user_id = uuid.uuid4()
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=f"{APP_URL}/agents/1", ok=True
    )
    app.dependency_overrides[optional_current_active_user] = lambda: SimpleNamespace(
        id=user_id
    )

    response = get_callback("state=s&code=c")

    assert response.status_code == 307
    admin.complete_oauth.assert_awaited_once_with(
        ChannelBotProvider.SLACK,
        ChannelBotOAuthCallback(state="s", code="c"),
        user_id=user_id,
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


UNUSABLE_APP_URLS = [
    "",
    "app.example.com",
    "https://",
    "ftp://app.example.com",
    "https://@",
    "https://app.example.com:bad",
]
APP_URL_MISSING = "Tracecat's public app URL is not configured."
APP_URL_ERROR = "Channel bot OAuth callback has no usable public app URL"


@pytest.fixture
def error_logs() -> Iterator[list[Record]]:
    records: list[Record] = []
    sink = logger.add(lambda message: records.append(message.record), level="ERROR")
    yield records
    logger.remove(sink)


def assert_app_url_refusal(
    response: httpx.Response, error_logs: list[Record], messages: list[str]
) -> None:
    assert response.status_code == 500
    assert response.text == APP_URL_MISSING
    assert "location" not in response.headers
    assert [r["message"] for r in error_logs] == messages
    logged = " ".join(f"{r['message']} {r['extra']}" for r in error_logs)
    assert "state-value" not in logged
    assert "code-value" not in logged


@pytest.mark.parametrize("app_url", UNUSABLE_APP_URLS)
@pytest.mark.parametrize(
    "return_url", ["https:///evil.example.net/x", "https:evil.example.net", "/x"]
)
def test_an_unusable_app_url_refuses_the_result_without_a_redirect(
    admin: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    error_logs: list[Record],
    app_url: str,
    return_url: str,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", app_url)
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=return_url, ok=True
    )

    response = get_callback("state=state-value&code=code-value")

    assert_app_url_refusal(response, error_logs, [APP_URL_ERROR])
    admin.complete_oauth.assert_awaited_once()


@pytest.mark.parametrize("app_url", UNUSABLE_APP_URLS)
def test_an_unusable_app_url_refuses_a_failing_admin_without_a_redirect(
    admin: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    error_logs: list[Record],
    app_url: str,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", app_url)
    admin.complete_oauth.side_effect = RuntimeError("boom")

    response = get_callback("state=state-value&code=code-value")

    assert_app_url_refusal(
        response, error_logs, ["Channel bot OAuth callback failed", APP_URL_ERROR]
    )


@pytest.mark.parametrize("app_url", UNUSABLE_APP_URLS)
def test_an_unusable_app_url_refuses_a_missing_admin_without_a_redirect(
    monkeypatch: pytest.MonkeyPatch, error_logs: list[Record], app_url: str
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", app_url)
    with_extensions(monkeypatch, other=PlainExtension())

    response = get_callback("state=state-value&code=code-value")

    assert_app_url_refusal(response, error_logs, [APP_URL_ERROR])


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


def test_callback_ignores_the_channel_entitlements(
    admin: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = grant(monkeypatch)
    admin.complete_oauth.return_value = ChannelBotOAuthResult(
        return_url=f"{APP_URL}/agents/1", ok=True
    )

    response = get_callback("state=s&code=c")

    assert response.status_code == 307
    assert location_query(response.headers["location"]) == {
        "channel_bot_install": ["success"]
    }
    check.assert_not_awaited()


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
    gate = router_module._require_agent_channels_preview
    assert any(dep.dependency is gate for dep in router_module.router.dependencies)
    assert not router_module.public_router.dependencies


def test_the_callback_stays_out_of_the_openapi_schema() -> None:
    assert not any(
        path.startswith("/agent/channel-bots/") for path in app.openapi()["paths"]
    )


@pytest.mark.parametrize("path", ["channels", "routable-channels"])
def test_channel_listings_declare_their_refusals(path: str) -> None:
    operation = app.openapi()["paths"][
        f"/workspaces/{{workspace_id}}/agent/channel-bots/{{bot_id}}/{path}"
    ]["get"]
    assert {"424", "501"} <= operation["responses"].keys()


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
