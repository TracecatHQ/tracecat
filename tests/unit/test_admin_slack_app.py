from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.admin.slack_app import router as router_module
from tracecat.agent.channel_bots import platform_app
from tracecat.agent.channel_bots.platform_app import (
    SLACK_PLATFORM_APP_KEY,
    SlackPlatformApp,
    read_slack_platform_app,
    slack_platform_app,
    write_slack_platform_app,
)
from tracecat.agent.channel_bots.schemas import (
    ChannelBotAppManifest,
    ChannelBotPlatformAppSetup,
    ChannelBotProvider,
    ChannelBotTransport,
)
from tracecat.audit.service import AuditService
from tracecat.auth.credentials import _require_superuser, _require_superuser_step_up
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import PlatformRole
from tracecat.auth.users import current_active_user
from tracecat.db.engine import get_async_session_bypass_rls
from tracecat.db.models import (
    AgentChannelBot,
    AgentPreset,
    PlatformSetting,
    User,
    Workspace,
)
from tracecat.secrets.encryption import encrypt_value

pytestmark = pytest.mark.usefixtures("db")

MANIFEST = ChannelBotAppManifest(
    filename="slack-app-manifest.json",
    media_type="application/json",
    content='{"settings": {}}',
    create_url="https://api.slack.com/apps?new_app=1&manifest_json=%7B%7D",
)
HTTP_SETUP = ChannelBotPlatformAppSetup(
    transport=ChannelBotTransport.HTTP,
    oauth_redirect_url="https://api.example.com/agent/channel-bots/slack/oauth/callback",
    request_url="https://api.example.com/platform-events",
    manifest=MANIFEST,
)
SOCKET_SETUP = HTTP_SETUP.model_copy(
    update={"transport": ChannelBotTransport.SOCKET, "request_url": None}
)
SECRETS = {
    "client_secret": "synthetic-client-secret",
    "signing_secret": "synthetic-signing-secret",
    "app_token": "xapp-synthetic-app-token",
}
FULL = {"app_id": "A0SYNTHETIC", "client_id": "111.222", **SECRETS}
REGISTERED = SlackPlatformApp.model_validate(FULL)


class ChannelBotAdmin:
    """The installed extension, as the admin routes ask it for the deployment's setup."""

    def __init__(self) -> None:
        self.setup = HTTP_SETUP
        self.asked: list[ChannelBotProvider] = []

    def platform_app_setup(
        self, provider: ChannelBotProvider
    ) -> ChannelBotPlatformAppSetup:
        self.asked.append(provider)
        return self.setup


@pytest.fixture
def channel_bot_admin(monkeypatch: pytest.MonkeyPatch) -> ChannelBotAdmin:
    admin = ChannelBotAdmin()
    monkeypatch.setattr(router_module, "get_channel_bot_admin", lambda: admin)
    return admin


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)
    return create_event


@pytest.fixture(autouse=True)
def requests_read_the_test_session(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> Iterator[None]:
    @asynccontextmanager
    async def bypass_session() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        platform_app, "get_async_session_bypass_rls_context_manager", bypass_session
    )
    slack_platform_app.cache_clear()
    yield
    slack_platform_app.cache_clear()


def _no_recent_security_key() -> PlatformRole:
    raise HTTPException(status_code=403, detail="Security key verification required")


def make_app(
    session: AsyncSession, *, superuser: bool, security_key: bool = True
) -> FastAPI:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/admin")
    user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password="synthetic",
        is_active=True,
        is_verified=True,
        is_superuser=superuser,
    )

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_active_user] = lambda: user
    app.dependency_overrides[get_async_session_bypass_rls] = test_session
    if superuser:
        role = PlatformRole(type="user", user_id=user.id, service_id="tracecat-api")
        # Past the IP allowlist and the security key step-up, which have their own tests.
        app.dependency_overrides[_require_superuser] = lambda: role
        app.dependency_overrides[_require_superuser_step_up] = (
            (lambda: role) if security_key else _no_recent_security_key
        )
    return app


@asynccontextmanager
async def client(
    session: AsyncSession, *, superuser: bool = True, security_key: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(
        app=make_app(session, superuser=superuser, security_key=security_key)
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://synthetic"
    ) as http:
        yield http


async def stored_row(session: AsyncSession) -> PlatformSetting | None:
    return await session.scalar(
        select(PlatformSetting).where(PlatformSetting.key == SLACK_PLATFORM_APP_KEY)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/admin/slack-app", None),
        ("PUT", "/admin/slack-app", FULL),
        ("DELETE", "/admin/slack-app", None),
        ("PATCH", "/admin/slack-app/setup", {"icon_uploaded": True}),
    ],
)
async def test_a_non_superuser_is_refused_on_every_route(
    session: AsyncSession,
    channel_bot_admin: ChannelBotAdmin,
    audit_events: AsyncMock,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    async with client(session, superuser=False) as http:
        response = await http.request(method, path, json=body)

    assert response.status_code == 403
    assert channel_bot_admin.asked == []
    assert await stored_row(session) is None


@pytest.mark.anyio
async def test_a_saved_app_is_stored_encrypted_and_never_returns_its_secrets(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    async with client(session) as http:
        saved = await http.put("/admin/slack-app", json=FULL)
        read = await http.get("/admin/slack-app")

    assert saved.status_code == read.status_code == 200
    for response in (saved, read):
        assert not any(secret in response.text for secret in SECRETS.values())
    assert read.json() == {
        "app_id": "A0SYNTHETIC",
        "client_id": "111.222",
        "client_secret_set": True,
        "signing_secret_set": True,
        "app_token_set": True,
        "icon_uploaded": False,
        "distribution_enabled": False,
        "unreadable": False,
        "setup": HTTP_SETUP.model_dump(mode="json"),
    }
    row = await stored_row(session)
    assert row is not None and row.is_encrypted
    assert not any(secret.encode() in row.value for secret in SECRETS.values())
    assert await read_slack_platform_app(session) == REGISTERED
    assert [c.kwargs["action"] for c in audit_events.await_args_list] == [
        "update",
        "update",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("dropped", "status", "named"),
    [
        ("client_secret", 400, "client secret"),
        ("signing_secret", 400, "signing secret"),
        ("app_id", 422, "app_id"),
        ("client_id", 422, "client_id"),
    ],
)
async def test_a_partial_registration_is_refused(
    session: AsyncSession,
    channel_bot_admin: ChannelBotAdmin,
    audit_events: AsyncMock,
    dropped: str,
    status: int,
    named: str,
) -> None:
    body = {k: v for k, v in FULL.items() if k != dropped}

    async with client(session) as http:
        response = await http.put("/admin/slack-app", json=body)

    assert response.status_code == status
    assert named in response.text
    assert await stored_row(session) is None


@pytest.mark.anyio
async def test_socket_mode_needs_the_app_token(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    channel_bot_admin.setup = SOCKET_SETUP
    without_token = {k: v for k, v in FULL.items() if k != "app_token"}

    async with client(session) as http:
        refused = await http.put("/admin/slack-app", json=without_token)
        saved = await http.put("/admin/slack-app", json=FULL)

    assert refused.status_code == 400
    assert "app-level token" in refused.json()["detail"]
    assert saved.status_code == 200
    assert saved.json()["app_token_set"] is True
    assert saved.json()["setup"]["request_url"] is None


@pytest.mark.anyio
async def test_an_omitted_secret_keeps_its_stored_value_only_for_the_same_app(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    async with client(session) as http:
        await http.put("/admin/slack-app", json=FULL)
        renamed = await http.put(
            "/admin/slack-app", json={"app_id": "A0SYNTHETIC", "client_id": "333.444"}
        )
        other_app = await http.put(
            "/admin/slack-app", json={"app_id": "A0OTHER", "client_id": "333.444"}
        )

    assert renamed.status_code == 200
    assert other_app.status_code == 400
    assert await read_slack_platform_app(session) == REGISTERED.model_copy(
        update={"client_id": "333.444"}
    )


@pytest.mark.anyio
async def test_removing_the_app_unregisters_it_for_the_next_request(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    async with client(session) as http:
        await http.put("/admin/slack-app", json=FULL)
        assert await slack_platform_app() == REGISTERED
        removed = await http.delete("/admin/slack-app")
        read = await http.get("/admin/slack-app")

    assert removed.status_code == 204
    assert await slack_platform_app() is None
    assert read.json()["app_id"] is None
    assert read.json()["client_secret_set"] is False
    assert await stored_row(session) is None
    assert audit_events.await_args_list[-1].kwargs["action"] == "delete"


async def add_bot(
    session: AsyncSession,
    workspace: Workspace,
    *,
    app_owner: str,
    status: str,
    tenant: str | None,
    app_id: str = FULL["app_id"],
) -> None:
    preset_id = uuid.uuid4()
    session.add(
        AgentPreset(
            id=preset_id,
            workspace_id=workspace.id,
            name="p",
            slug=f"p-{preset_id.hex}",
            model_name="m",
            model_provider="p",
        )
    )
    await session.flush()
    session.add(
        AgentChannelBot(
            workspace_id=workspace.id,
            agent_preset_id=preset_id,
            provider="slack",
            app_owner=app_owner,
            status=status,
            provider_tenant_id=tenant,
            # Set with the tenant when an install finishes.
            provider_app_id=app_id if tenant else None,
            granted_scopes=[],
            manifest_version=0,
            encrypted_credentials=b"sealed",
        )
    )
    await session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("app_owner", "status", "tenant", "refused"),
    [
        ("platform", "active", "T1", True),
        ("platform", "paused", "T1", True),
        ("platform", "needs_reconnect", "T1", True),
        # The browser never came back from Slack: nothing is installed.
        ("platform", "needs_reconnect", None, False),
        ("platform", "disconnected", "T1", False),
        ("workspace", "active", "T1", False),
    ],
)
async def test_removing_the_app_is_refused_while_an_organization_has_it_installed(
    session: AsyncSession,
    svc_workspace: Workspace,
    channel_bot_admin: ChannelBotAdmin,
    audit_events: AsyncMock,
    app_owner: str,
    status: str,
    tenant: str | None,
    refused: bool,
) -> None:
    await add_bot(
        session, svc_workspace, app_owner=app_owner, status=status, tenant=tenant
    )

    async with client(session) as http:
        await http.put("/admin/slack-app", json=FULL)
        removed = await http.delete("/admin/slack-app")

    if refused:
        assert removed.status_code == 409
        assert "1 Slack workspace(s) still have" in removed.json()["detail"]
        assert await read_slack_platform_app(session) == REGISTERED
        assert await slack_platform_app() == REGISTERED
    else:
        assert removed.status_code == 204
        assert await stored_row(session) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "refused"), [("active", True), ("disconnected", False)]
)
async def test_replacing_the_app_is_refused_while_an_organization_has_it_installed(
    session: AsyncSession,
    svc_workspace: Workspace,
    channel_bot_admin: ChannelBotAdmin,
    audit_events: AsyncMock,
    status: str,
    refused: bool,
) -> None:
    await add_bot(
        session, svc_workspace, app_owner="platform", status=status, tenant="T1"
    )
    other = {**FULL, "app_id": "A0OTHER", "client_id": "333.444"}

    async with client(session) as http:
        await http.put("/admin/slack-app", json=FULL)
        same_app = await http.put(
            "/admin/slack-app", json={**FULL, "client_id": "555.666"}
        )
        replaced = await http.put("/admin/slack-app", json=other)

    assert same_app.status_code == 200
    stored = await read_slack_platform_app(session)
    assert stored is not None
    if refused:
        assert replaced.status_code == 409
        assert "before replacing it with another Slack app" in replaced.json()["detail"]
        assert stored.app_id == FULL["app_id"]
    else:
        assert replaced.status_code == 200
        assert stored.app_id == "A0OTHER"


@pytest.mark.anyio
async def test_a_rotating_app_token_is_saved_with_its_refresh_token(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    channel_bot_admin.setup = SOCKET_SETUP
    rotating = {**FULL, "app_token": "xoxe.xapp-synthetic-rotating"}

    async with client(session) as http:
        without_refresh = await http.put("/admin/slack-app", json=rotating)
        refresh_without_rotation = await http.put(
            "/admin/slack-app",
            json={**FULL, "app_refresh_token": "xoxe-synthetic-refresh"},
        )
        saved = await http.put(
            "/admin/slack-app",
            json={**rotating, "app_refresh_token": "xoxe-synthetic-refresh"},
        )
        kept = await http.put(
            "/admin/slack-app", json={"app_id": "A0SYNTHETIC", "client_id": "111.222"}
        )

    assert without_refresh.status_code == refresh_without_rotation.status_code == 422
    assert saved.status_code == kept.status_code == 200
    assert "xoxe" not in saved.text
    stored = await read_slack_platform_app(session)
    assert stored is not None
    assert stored.app_token == "xoxe.xapp-synthetic-rotating"
    assert stored.app_refresh_token == "xoxe-synthetic-refresh"


async def stored_refresh_due_at(session: AsyncSession) -> datetime | None:
    stored = await read_slack_platform_app(session)
    assert stored is not None
    return stored.app_token_refresh_due_at


@pytest.mark.anyio
async def test_a_rotating_app_token_is_due_a_refresh_from_when_it_is_saved(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    rotating = {
        **FULL,
        "app_token": "xoxe.xapp-synthetic-rotating",
        "app_refresh_token": "xoxe-synthetic-refresh",
    }
    without_token = {k: v for k, v in FULL.items() if k != "app_token"}

    async with client(session) as http:
        before = datetime.now(UTC)
        await http.put("/admin/slack-app", json=rotating)
        after = datetime.now(UTC)
        due = await stored_refresh_due_at(session)
        await http.put("/admin/slack-app", json=without_token)
        kept = await stored_refresh_due_at(session)
        await http.put("/admin/slack-app", json=FULL)
        not_rotating = await stored_refresh_due_at(session)
        await http.put("/admin/slack-app", json={**without_token, "app_id": "A0OTHER"})
        no_token = await stored_refresh_due_at(session)

    assert due is not None and before <= due <= after
    assert kept == due
    assert not_rotating is None
    assert no_token is None


@pytest.mark.anyio
async def test_a_registration_that_no_longer_decrypts_reads_as_unreadable(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    session.add(
        PlatformSetting(
            key=SLACK_PLATFORM_APP_KEY,
            value=b"not-a-ciphertext",
            value_type="json",
            is_encrypted=True,
        )
    )
    await session.commit()

    async with client(session) as http:
        read = await http.get("/admin/slack-app")
        saved = await http.put("/admin/slack-app", json=FULL)

    assert read.json()["unreadable"] is True
    assert read.json()["app_id"] is None
    assert await slack_platform_app() is not None
    assert saved.status_code == 200
    assert saved.json()["unreadable"] is False


@pytest.mark.anyio
async def test_an_unreadable_registration_is_replaced_only_by_its_installs_app(
    session: AsyncSession,
    svc_workspace: Workspace,
    channel_bot_admin: ChannelBotAdmin,
    audit_events: AsyncMock,
) -> None:
    await add_bot(
        session, svc_workspace, app_owner="platform", status="active", tenant="T1"
    )
    session.add(
        PlatformSetting(
            key=SLACK_PLATFORM_APP_KEY,
            value=b"not-a-ciphertext",
            value_type="json",
            is_encrypted=True,
        )
    )
    await session.commit()

    async with client(session) as http:
        other = await http.put(
            "/admin/slack-app",
            json={**FULL, "app_id": "A0OTHER", "client_id": "333.444"},
        )
        same = await http.put("/admin/slack-app", json=FULL)

    assert other.status_code == 409
    assert same.status_code == 200
    assert await read_slack_platform_app(session) == REGISTERED


@pytest.mark.anyio
async def test_setup_steps_marked_done_persist_across_a_read(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    async with client(session) as http:
        await http.put("/admin/slack-app", json=FULL)
        icon = await http.patch("/admin/slack-app/setup", json={"icon_uploaded": True})
        after_icon = await http.get("/admin/slack-app")
        both = await http.patch(
            "/admin/slack-app/setup", json={"distribution_enabled": True}
        )
        after_both = await http.get("/admin/slack-app")

    assert icon.status_code == both.status_code == 200
    assert icon.json() == after_icon.json()
    assert (
        after_icon.json()["icon_uploaded"],
        after_icon.json()["distribution_enabled"],
    ) == (True, False)
    assert both.json() == after_both.json()
    assert (
        after_both.json()["icon_uploaded"],
        after_both.json()["distribution_enabled"],
    ) == (True, True)
    assert await read_slack_platform_app(session) == REGISTERED.model_copy(
        update={"icon_uploaded": True, "distribution_enabled": True}
    )
    assert audit_events.await_args_list[-1].kwargs["action"] == "update"


@pytest.mark.anyio
async def test_a_registration_stored_before_the_setup_steps_reads_them_as_not_done(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin
) -> None:
    session.add(
        PlatformSetting(
            key=SLACK_PLATFORM_APP_KEY,
            value=encrypt_value(json.dumps(FULL).encode(), key=get_db_encryption_key()),
            value_type="json",
            is_encrypted=True,
        )
    )
    await session.commit()

    async with client(session) as http:
        read = await http.get("/admin/slack-app")

    assert read.json()["app_id"] == "A0SYNTHETIC"
    assert read.json()["unreadable"] is False
    assert read.json()["icon_uploaded"] is False
    assert read.json()["distribution_enabled"] is False


@pytest.mark.anyio
async def test_saving_another_app_resets_the_setup_steps_and_the_same_app_keeps_them(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    done = {"icon_uploaded": True, "distribution_enabled": True}
    async with client(session) as http:
        await http.put("/admin/slack-app", json=FULL)
        await http.patch("/admin/slack-app/setup", json=done)
        same_app = await http.put(
            "/admin/slack-app", json={"app_id": "A0SYNTHETIC", "client_id": "333.444"}
        )
        other_app = await http.put(
            "/admin/slack-app", json={**FULL, "app_id": "A0OTHER"}
        )

    assert same_app.status_code == other_app.status_code == 200
    assert (
        same_app.json()["icon_uploaded"],
        same_app.json()["distribution_enabled"],
    ) == (True, True)
    assert (
        other_app.json()["icon_uploaded"],
        other_app.json()["distribution_enabled"],
    ) == (False, False)
    stored = await read_slack_platform_app(session)
    assert stored is not None
    assert (stored.icon_uploaded, stored.distribution_enabled) == (False, False)


@pytest.mark.anyio
async def test_marking_a_setup_step_done_without_a_registered_app_is_not_found(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    async with client(session) as http:
        response = await http.patch(
            "/admin/slack-app/setup", json={"icon_uploaded": True}
        )

    assert response.status_code == 404
    assert await stored_row(session) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body", [{}, {"icon_uploaded": None}, {"icon_uploaded": True, "app_id": "A0X"}]
)
async def test_marking_setup_steps_needs_one_known_step(
    session: AsyncSession,
    channel_bot_admin: ChannelBotAdmin,
    audit_events: AsyncMock,
    body: dict[str, object],
) -> None:
    await write_slack_platform_app(session, REGISTERED)
    await session.commit()

    async with client(session) as http:
        response = await http.patch("/admin/slack-app/setup", json=body)

    assert response.status_code == 422
    assert await read_slack_platform_app(session) == REGISTERED


@pytest.mark.anyio
async def test_marking_setup_steps_does_not_need_a_recent_security_key(
    session: AsyncSession, channel_bot_admin: ChannelBotAdmin, audit_events: AsyncMock
) -> None:
    await write_slack_platform_app(session, REGISTERED)
    await session.commit()

    async with client(session, security_key=False) as http:
        marked = await http.patch(
            "/admin/slack-app/setup", json={"distribution_enabled": True}
        )
        saved = await http.put("/admin/slack-app", json=FULL)

    assert saved.status_code == 403
    assert marked.status_code == 200
    assert marked.json()["distribution_enabled"] is True
