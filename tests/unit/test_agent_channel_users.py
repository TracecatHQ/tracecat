"""Channel senders linked to Tracecat users, and who may act through a channel."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import get_args
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_workspace_membership
from tracecat import config
from tracecat.agent.channel_bots.messages import ChannelMessage, ChannelMessageSender
from tracecat.agent.channel_bots.schemas import ChannelBotProvider
from tracecat.agent.channel_users import router as channel_users_router
from tracecat.agent.channel_users import service as channel_users_service
from tracecat.agent.channel_users import tokens
from tracecat.agent.channel_users.enums import ChannelAccessPolicy, ChannelAction
from tracecat.agent.channel_users.service import (
    ChannelApproverNotAllowed,
    ChannelLinkRequired,
    check_channel_access,
    complete_channel_link,
    linked_member_names,
    resolve_channel_actor,
    start_provider_sign_in,
    unlink_channel_user,
)
from tracecat.agent.channel_users.tokens import (
    ChannelLinkInvalid,
    mint_link_token,
    mint_sign_in_state,
    read_link_token,
    read_sign_in_state,
)
from tracecat.agent.channel_users.types import ChannelIdentity, ProviderSignIn
from tracecat.agent.session.enums import UserMessageOrigin
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.api.app import (
    entitlement_exception_handler,
    scope_denied_exception_handler,
)
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.types import Role
from tracecat.authz.membership import ensure_member
from tracecat.authz.seeding import seed_system_roles_for_org, seed_system_scopes
from tracecat.db.engine import get_async_session
from tracecat.db.models import (
    AgentChannelBot,
    AgentChannelUser,
    AgentPreset,
    AgentSession,
    AgentSessionHistory,
    AgentSessionThread,
    Organization,
    OrganizationTier,
    Tier,
    User,
    Workspace,
)
from tracecat.exceptions import EntitlementRequired, ScopeDeniedError
from tracecat.redis.client import RedisClient
from tracecat.tiers.enums import Entitlement

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]

PROVIDER = "slack"
TENANT = "T0000TEST"


@dataclass(frozen=True, slots=True)
class _Org:
    organization_id: uuid.UUID
    workspace_id: uuid.UUID


def _identity(provider_user_id: str) -> ChannelIdentity:
    return ChannelIdentity(PROVIDER, TENANT, provider_user_id)


async def _user(
    session: AsyncSession,
    org: _Org,
    *,
    member: bool = True,
    workspace_role: str | None = "workspace-editor",
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()
    if member:
        await ensure_member(session, org.organization_id, user.id)
        if workspace_role is not None:
            await grant_workspace_membership(
                session,
                user_id=user.id,
                organization_id=org.organization_id,
                workspace_id=org.workspace_id,
                slug=workspace_role,
            )
    await session.commit()
    return user


async def _link(
    session: AsyncSession,
    org: _Org,
    provider_user_id: str,
    user: User,
) -> None:
    session.add(
        AgentChannelUser(
            organization_id=org.organization_id,
            provider=PROVIDER,
            provider_tenant_id=TENANT,
            provider_user_id=provider_user_id,
            user_id=user.id,
        )
    )
    await session.commit()


async def _entitle(
    session: AsyncSession,
    organization_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entitled: bool = True,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    tier = Tier(
        display_name=f"Tier {uuid.uuid4().hex[:8]}",
        entitlements={Entitlement.AGENT_CHANNELS_PREVIEW.value: entitled},
        is_default=False,
        sort_order=0,
        is_active=True,
    )
    session.add(tier)
    await session.flush()
    session.add(OrganizationTier(organization_id=organization_id, tier_id=tier.id))
    await session.commit()


@pytest.fixture(autouse=True)
def fresh_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared client is bound to the event loop of the test that created it.
    monkeypatch.setattr("tracecat.redis.client._redis_client", None)
    for attribute in ("_client", "_pool", "_loop"):
        monkeypatch.setattr(RedisClient, attribute, None)


@pytest.fixture
async def org(session: AsyncSession, svc_role: Role) -> _Org:
    assert svc_role.organization_id is not None and svc_role.workspace_id is not None
    await seed_system_scopes(session)
    await seed_system_roles_for_org(session, svc_role.organization_id)
    return _Org(svc_role.organization_id, svc_role.workspace_id)


SENDER_STATES = ("linked_editor", "linked_viewer", "unlinked", "non_member")


async def _sender(session: AsyncSession, org: _Org, state: str) -> ChannelIdentity:
    provider_user_id = f"U{uuid.uuid4().hex[:8]}"
    match state:
        case "linked_editor" | "linked_viewer":
            role = (
                "workspace-editor" if state == "linked_editor" else "workspace-viewer"
            )
            user = await _user(session, org, workspace_role=role)
            await _link(session, org, provider_user_id, user)
        case "non_member":
            user = await _user(session, org, member=False)
            await _link(session, org, provider_user_id, user)
    return _identity(provider_user_id)


OK, LINK, DENY = None, ChannelLinkRequired, ChannelApproverNotAllowed
_P, _A = ChannelAccessPolicy, ChannelAction
# Columns follow SENDER_STATES.
EXPECTED_ROWS: dict[
    tuple[ChannelAccessPolicy, ChannelAction], tuple[type[Exception] | None, ...]
] = {
    (_P.OPEN, _A.MESSAGE): (OK, OK, OK, OK),
    (_P.OPEN, _A.STOP): (OK, OK, OK, OK),
    (_P.OPEN, _A.APPROVE): (OK, OK, OK, OK),
    (_P.LINKED_APPROVALS, _A.MESSAGE): (OK, OK, OK, OK),
    (_P.LINKED_APPROVALS, _A.STOP): (OK, OK, OK, OK),
    (_P.LINKED_APPROVALS, _A.APPROVE): (OK, DENY, LINK, LINK),
    (_P.LINKED_ONLY, _A.MESSAGE): (OK, OK, LINK, LINK),
    (_P.LINKED_ONLY, _A.STOP): (OK, OK, LINK, LINK),
    (_P.LINKED_ONLY, _A.APPROVE): (OK, DENY, LINK, LINK),
}
EXPECTED = {
    (policy, action, state): outcome
    for (policy, action), row in EXPECTED_ROWS.items()
    for state, outcome in zip(SENDER_STATES, row, strict=True)
}


@pytest.mark.parametrize("state", SENDER_STATES)
@pytest.mark.parametrize("action", list(ChannelAction))
@pytest.mark.parametrize("policy", list(ChannelAccessPolicy))
async def test_channel_access_matrix(
    session: AsyncSession,
    org: _Org,
    policy: ChannelAccessPolicy,
    action: ChannelAction,
    state: str,
) -> None:
    identity = await _sender(session, org, state)
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=identity
    )
    expected = EXPECTED[policy, action, state]
    check = check_channel_access(
        session,
        policy=policy,
        action=action,
        actor=actor,
        workspace_id=org.workspace_id,
    )
    if expected is None:
        await check
    else:
        with pytest.raises(expected):
            await check


async def test_a_matching_email_does_not_link_a_sender(
    session: AsyncSession, org: _Org
) -> None:
    await _user(session, org)
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=_identity("U1")
    )
    assert actor.user_id is None
    assert await session.scalar(select(AgentChannelUser.id)) is None


async def test_same_provider_user_is_isolated_per_organization(
    session: AsyncSession, org: _Org, svc_role: Role
) -> None:
    other_org_row = Organization(
        name="Other", slug=f"other-{uuid.uuid4().hex[:8]}", is_active=True
    )
    session.add(other_org_row)
    await session.commit()
    other = _Org(other_org_row.id, org.workspace_id)
    user_a = await _user(session, org)
    user_b = await _user(session, other, workspace_role=None)
    await _link(session, org, "U5", user_a)

    in_b = await resolve_channel_actor(
        session, organization_id=other.organization_id, identity=_identity("U5")
    )
    assert in_b.user_id is None
    await _link(session, other, "U5", user_b)
    in_a = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=_identity("U5")
    )
    in_b = await resolve_channel_actor(
        session, organization_id=other.organization_id, identity=_identity("U5")
    )
    assert (in_a.user_id, in_b.user_id) == (user_a.id, user_b.id)


async def test_linked_member_names_cover_current_members_only(
    session: AsyncSession, org: _Org
) -> None:
    named = await _user(session, org)
    named.first_name, named.last_name = "Ada", "Lovelace"
    unnamed = await _user(session, org)
    gone = await _user(session, org)
    await session.commit()
    await _link(session, org, "U1", named)
    await _link(session, org, "U2", unnamed)
    await _link(session, org, "U4", gone)
    gone.is_active = False
    await session.commit()

    names = await linked_member_names(
        session,
        organization_id=org.organization_id,
        identities=[_identity(f"U{n}") for n in range(1, 6)],
    )

    # Deactivated and unlinked users get no Tracecat name.
    assert names == {_identity("U1"): "Ada Lovelace", _identity("U2"): unnamed.email}


class _SignedInAs:
    """The installed bot admin's provider sign-in, answering with one identity."""

    def __init__(self, identity: ChannelIdentity) -> None:
        self.identity = identity

    async def verify_provider_sign_in(self, *args: object, **kwargs: object):
        return ProviderSignIn(self.identity, email="signed-in@example.com")


def _sign_in(org: _Org, provider_user_id: str, user: User) -> str:
    token = mint_link_token(
        organization_id=org.organization_id,
        workspace_id=org.workspace_id,
        bot_id=uuid.uuid4(),
        identity=ChannelIdentity(PROVIDER, TENANT, provider_user_id),
        interaction_id="interaction",
    )
    return mint_sign_in_state(link_token=token, user_id=user.id)


async def test_challenge_links_once_and_replaces_an_earlier_link(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    earlier = await _user(session, org)
    user = await _user(session, org)
    await _link(session, org, "U6", earlier)
    admin = _SignedInAs(ChannelIdentity(PROVIDER, TENANT, "U6"))
    monkeypatch.setattr(
        channel_users_service, "require_channel_bot_admin", lambda: admin
    )
    sign_in = read_sign_in_state(_sign_in(org, "U6", user))

    await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=_identity("U6")
    )
    assert actor.user_id == user.id
    link = await session.scalar(
        select(AgentChannelUser).where(AgentChannelUser.provider_user_id == "U6")
    )
    assert link is not None and link.provider_email == "signed-in@example.com"
    with pytest.raises(ChannelLinkInvalid, match="already used"):
        await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    # A used link never reaches the provider again.
    with pytest.raises(ChannelLinkInvalid, match="already used"):
        await start_provider_sign_in(
            session, link_token=sign_in.link_token, user_id=user.id
        )


@pytest.mark.parametrize("refusal", ["identity", "other_user", "non_member"])
async def test_challenge_refusals_write_nothing(
    session: AsyncSession,
    org: _Org,
    monkeypatch: pytest.MonkeyPatch,
    refusal: str,
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    user = await _user(session, org, member=refusal != "non_member")
    signed_in = "U-other" if refusal == "identity" else "U7"
    admin = _SignedInAs(ChannelIdentity(PROVIDER, TENANT, signed_in))
    monkeypatch.setattr(
        channel_users_service, "require_channel_bot_admin", lambda: admin
    )
    sign_in = read_sign_in_state(_sign_in(org, "U7", user))
    confirming = uuid.uuid4() if refusal == "other_user" else user.id

    with pytest.raises(ChannelLinkInvalid):
        await complete_channel_link(
            session, sign_in=sign_in, code="c", user_id=confirming
        )
    assert await session.scalar(select(AgentChannelUser.id)) is None


async def test_a_wrong_account_sign_in_leaves_the_link_usable(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    user = await _user(session, org)
    admin = _SignedInAs(ChannelIdentity(PROVIDER, TENANT, "U-other"))
    monkeypatch.setattr(
        channel_users_service, "require_channel_bot_admin", lambda: admin
    )
    sign_in = read_sign_in_state(_sign_in(org, "U11", user))

    with pytest.raises(ChannelLinkInvalid, match="different provider user"):
        await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    admin.identity = ChannelIdentity(PROVIDER, TENANT, "U11")
    await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)

    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=_identity("U11")
    )
    assert actor.user_id == user.id


async def test_a_failed_link_write_leaves_the_link_usable(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    user = await _user(session, org)
    admin = _SignedInAs(ChannelIdentity(PROVIDER, TENANT, "U12"))
    monkeypatch.setattr(
        channel_users_service, "require_channel_bot_admin", lambda: admin
    )
    sign_in = read_sign_in_state(_sign_in(org, "U12", user))
    upsert_link = channel_users_service._upsert_link

    async def failing_upsert(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(channel_users_service, "_upsert_link", failing_upsert)
    with pytest.raises(RuntimeError):
        await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    monkeypatch.setattr(channel_users_service, "_upsert_link", upsert_link)
    await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)

    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=_identity("U12")
    )
    assert actor.user_id == user.id


async def _thread_session(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    """A session linked to a Slack thread of a bot in the workspace."""
    preset = AgentPreset(
        workspace_id=workspace_id,
        name="p",
        slug=f"p-{uuid.uuid4().hex}",
        model_name="m",
        model_provider="p",
    )
    session.add(preset)
    await session.flush()
    bot = AgentChannelBot(
        workspace_id=workspace_id,
        agent_preset_id=preset.id,
        provider=PROVIDER,
        manifest_version=1,
        encrypted_credentials=b"",
    )
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="thread",
        entity_type=AgentSessionEntity.EXTERNAL_CHANNEL.value,
        entity_id=uuid.uuid4(),
    )
    session.add_all([bot, agent_session])
    await session.flush()
    session.add(
        AgentSessionThread(
            session_id=agent_session.id,
            workspace_id=workspace_id,
            bot_id=bot.id,
            provider_thread_id={"thread": uuid.uuid4().hex},
        )
    )
    await session.flush()
    return agent_session.id


async def _channel_prompt(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    sender: ChannelIdentity,
    author_id: uuid.UUID | None = None,
) -> uuid.UUID:
    text = ChannelMessage(
        provider=ChannelBotProvider.SLACK,
        text="hello",
        sender=ChannelMessageSender(
            provider_user_id=sender.provider_user_id,
            provider_team_id=sender.provider_tenant_id,
        ),
    ).render()
    row = AgentSessionHistory(
        workspace_id=workspace_id,
        session_id=session_id,
        content={"type": "user", "message": {"role": "user", "content": text}},
        kind="chat-message",
        origin=UserMessageOrigin.CHANNEL,
        author_id=author_id,
    )
    session.add(row)
    await session.flush()
    return row.id


async def test_linking_authors_only_the_senders_earlier_messages_in_the_org(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    user = await _user(session, org)
    earlier = await _user(session, org)
    other_org = Organization(
        name="Other", slug=f"other-{uuid.uuid4().hex[:8]}", is_active=True
    )
    session.add(other_org)
    await session.flush()
    other_workspace = Workspace(name="Other", organization_id=other_org.id)
    session.add(other_workspace)
    await session.flush()
    here = await _thread_session(session, org.workspace_id)
    there = await _thread_session(session, other_workspace.id)
    sender = _identity("U13")
    sent = await _channel_prompt(session, org.workspace_id, here, sender)
    untouched = [
        await _channel_prompt(session, org.workspace_id, here, _identity("U130")),
        await _channel_prompt(
            session, org.workspace_id, here, ChannelIdentity(PROVIDER, "T-other", "U13")
        ),
        await _channel_prompt(session, other_workspace.id, there, sender),
    ]
    authored = await _channel_prompt(
        session, org.workspace_id, here, sender, author_id=earlier.id
    )
    await session.commit()
    admin = _SignedInAs(sender)
    monkeypatch.setattr(
        channel_users_service, "require_channel_bot_admin", lambda: admin
    )
    sign_in = read_sign_in_state(_sign_in(org, "U13", user))

    await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)

    authors = dict(
        (
            await session.execute(
                select(AgentSessionHistory.id, AgentSessionHistory.author_id)
            )
        ).tuples()
    )
    assert authors[sent] == user.id
    assert authors[authored] == earlier.id
    assert [authors[row_id] for row_id in untouched] == [None, None, None]


async def test_challenge_needs_the_channels_preview(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch, entitled=False)
    user = await _user(session, org)
    sign_in = read_sign_in_state(_sign_in(org, "U8", user))

    with pytest.raises(EntitlementRequired):
        await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    assert await session.scalar(select(AgentChannelUser.id)) is None


async def test_expired_or_tampered_tokens_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = ChannelIdentity(PROVIDER, TENANT, "U8")
    kwargs = {
        "organization_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "bot_id": uuid.uuid4(),
        "identity": identity,
        "interaction_id": "interaction",
    }
    token = mint_link_token(**kwargs)
    with pytest.raises(ChannelLinkInvalid):
        read_link_token(token[:-2] + "xx")
    with pytest.raises(ChannelLinkInvalid):
        read_sign_in_state(token)
    monkeypatch.setattr(tokens, "LINK_TOKEN_TTL", timedelta(seconds=-60))
    with pytest.raises(ChannelLinkInvalid):
        read_link_token(mint_link_token(**kwargs))


async def test_unlink_removes_future_authority(
    session: AsyncSession, org: _Org
) -> None:
    user = await _user(session, org)
    stranger = await _user(session, org)
    await _link(session, org, "U9", user)
    link_id = await session.scalar(
        select(AgentChannelUser.id).where(AgentChannelUser.provider_user_id == "U9")
    )
    assert link_id is not None
    assert not await unlink_channel_user(
        session,
        organization_id=org.organization_id,
        link_id=link_id,
        user_id=stranger.id,
    )
    assert await unlink_channel_user(
        session, organization_id=org.organization_id, link_id=link_id, user_id=user.id
    )
    assert await session.scalar(select(AgentChannelUser.id)) is None
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, identity=_identity("U9")
    )
    assert actor.user_id is None
    with pytest.raises(ChannelLinkRequired):
        await check_channel_access(
            session,
            policy=ChannelAccessPolicy.LINKED_APPROVALS,
            action=ChannelAction.APPROVE,
            actor=actor,
            workspace_id=org.workspace_id,
        )


async def test_bot_policy_defaults_to_linked_approvals(
    session: AsyncSession, svc_role: Role
) -> None:
    assert svc_role.workspace_id is not None
    preset = AgentPreset(
        workspace_id=svc_role.workspace_id,
        name="Responder",
        slug=f"responder-{uuid.uuid4().hex[:8]}",
        model_name="gpt-5",
        model_provider="openai",
    )
    session.add(preset)
    await session.flush()
    bot = AgentChannelBot(
        workspace_id=svc_role.workspace_id,
        agent_preset_id=preset.id,
        provider="slack",
        manifest_version=1,
        encrypted_credentials=b"",
    )
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    assert bot.channel_access_policy == ChannelAccessPolicy.LINKED_APPROVALS


APP_URL = "https://app.example.com"


def _redirect_query(response) -> tuple[str, dict[str, list[str]]]:
    location = urlparse(response.headers["location"])
    return location.path, parse_qs(location.query)


async def test_callback_redirects_a_cancel_or_signed_out_browser_to_the_page(
    org: _Org, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    user = await _user(session, org)
    state = _sign_in(org, "U10", user)
    token = read_sign_in_state(state).link_token
    callback = channel_users_router.handle_channel_link_callback

    cancelled = await callback(user=user, state=state, error="access_denied")
    signed_out = await callback(user=None, state=state, code="c")
    unreadable = await callback(user=user, state="garbage", code="c")

    assert _redirect_query(cancelled) == (
        f"/channel-links/{token}",
        {
            "result": ["error"],
            "error": ["cancelled"],
            "provider": [PROVIDER],
            "tenant": [TENANT],
        },
    )
    assert _redirect_query(signed_out)[1] == {
        "result": ["error"],
        "error": ["signed_out"],
        "provider": [PROVIDER],
        "tenant": [TENANT],
    }
    assert _redirect_query(unreadable) == (
        "/channel-links/invalid",
        {"result": ["error"], "error": ["invalid"]},
    )


async def test_callback_names_the_provider_and_tenant_on_success(
    org: _Org, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    user = await _user(session, org)
    state = _sign_in(org, "U11", user)
    token = read_sign_in_state(state).link_token

    async def _linked(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(channel_users_router, "complete_channel_link", _linked)
    linked = await channel_users_router.handle_channel_link_callback(
        user=user, state=state, code="c"
    )

    assert _redirect_query(linked) == (
        f"/channel-links/{token}",
        {
            "result": ["linked"],
            "provider": [PROVIDER],
            "tenant": [TENANT],
        },
    )


async def test_link_routes_enforce_the_org_ip_allowlist(
    org: _Org, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    user = await _user(session, org)
    state = _sign_in(org, "U12", user)
    checked: list[uuid.UUID] = []

    async def _deny(organization_id: uuid.UUID, *, bypass: bool = False) -> None:
        checked.append(organization_id)
        raise HTTPException(status_code=403, detail="blocked")

    async def _never(*_args, **_kwargs) -> None:
        raise AssertionError("linked from a blocked network")

    monkeypatch.setattr(channel_users_router, "enforce_org_ip_allowlist", _deny)
    monkeypatch.setattr(channel_users_router, "complete_channel_link", _never)
    blocked = await channel_users_router.handle_channel_link_callback(
        user=user, state=state, code="c"
    )
    assert _redirect_query(blocked)[1]["error"] == ["blocked_network"]
    with pytest.raises(HTTPException) as exc_info:
        await channel_users_router.start_channel_link_sign_in(
            user=user, token=read_sign_in_state(state).link_token
        )
    assert exc_info.value.status_code == 403
    assert checked == [org.organization_id, org.organization_id]


async def test_an_allowlist_lookup_failure_redirects_as_failed(
    org: _Org, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    user = await _user(session, org)

    async def _broken(*_args, **_kwargs) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(channel_users_router, "enforce_org_ip_allowlist", _broken)
    failed = await channel_users_router.handle_channel_link_callback(
        user=user, state=_sign_in(org, "U13", user), code="c"
    )
    assert _redirect_query(failed)[1]["error"] == ["failed"]


@asynccontextmanager
async def _admin_client(
    session: AsyncSession, org: _Org, scopes: frozenset[str]
) -> AsyncIterator[httpx.AsyncClient]:
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        organization_id=org.organization_id,
        scopes=scopes,
    )
    test_app = FastAPI()
    test_app.include_router(channel_users_router.router)
    test_app.add_exception_handler(EntitlementRequired, entitlement_exception_handler)
    test_app.add_exception_handler(ScopeDeniedError, scope_denied_exception_handler)
    test_app.dependency_overrides.update(
        {
            get_args(OrgUserRole)[1].dependency: lambda: role,
            get_async_session: lambda: session,
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


async def test_a_plain_member_cannot_list_the_organization_links(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    async with _admin_client(session, org, frozenset({"org:member:read"})) as client:
        response = await client.get("/agent/channel-users")
    async with _admin_client(session, org, frozenset({"org:member:remove"})) as client:
        allowed = await client.get("/agent/channel-users")

    assert response.status_code == 403
    assert response.json()["error"]["missing_scopes"] == ["org:member:remove"]
    assert allowed.status_code == 200


async def test_channel_user_routes_need_the_channels_preview(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch, entitled=False)
    async with _admin_client(session, org, frozenset({"org:member:remove"})) as client:
        response = await client.get("/agent/channel-users/me")

    assert response.status_code == 403
    assert response.json()["type"] == "EntitlementRequired"
