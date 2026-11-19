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
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_workspace_membership
from tracecat import config
from tracecat.agent.channel_users import router as channel_users_router
from tracecat.agent.channel_users import service as channel_users_service
from tracecat.agent.channel_users import tokens
from tracecat.agent.channel_users.enums import (
    ChannelAccessPolicy,
    ChannelAction,
    ChannelLinkMethod,
)
from tracecat.agent.channel_users.service import (
    ChannelApproverNotAllowed,
    ChannelLinkRequired,
    check_channel_access,
    complete_channel_link,
    read_link_details,
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
from tracecat.agent.channel_users.types import ChannelIdentity, ChannelSenderEvidence
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
    Organization,
    OrganizationDomain,
    OrganizationTier,
    Tier,
    User,
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
    domain: str


def _evidence(
    provider_user_id: str,
    email: str | None = None,
    *,
    email_confirmed: bool = True,
    is_guest: bool = False,
    is_external: bool = False,
) -> ChannelSenderEvidence:
    return ChannelSenderEvidence(
        provider=PROVIDER,
        provider_tenant_id=TENANT,
        provider_user_id=provider_user_id,
        email=email,
        email_confirmed=email_confirmed,
        is_guest=is_guest,
        is_external=is_external,
    )


async def _add_domain(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    is_active: bool = True,
    verification_method: str = "platform_admin",
) -> str:
    domain = f"{uuid.uuid4().hex[:12]}.example.com"
    session.add(
        OrganizationDomain(
            organization_id=organization_id,
            domain=domain,
            normalized_domain=domain,
            is_active=is_active,
            verification_method=verification_method,
        )
    )
    await session.commit()
    return domain


async def _user(
    session: AsyncSession,
    org: _Org,
    *,
    member: bool = True,
    workspace_role: str | None = "workspace-editor",
    domain: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@{domain or org.domain}",
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
    user: User | None,
    method: ChannelLinkMethod = ChannelLinkMethod.CHALLENGE,
) -> None:
    session.add(
        AgentChannelUser(
            organization_id=org.organization_id,
            provider=PROVIDER,
            provider_tenant_id=TENANT,
            provider_user_id=provider_user_id,
            user_id=None if user is None else user.id,
            link_method=method,
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
    domain = await _add_domain(session, svc_role.organization_id)
    return _Org(svc_role.organization_id, svc_role.workspace_id, domain)


SENDER_STATES = (
    "linked_editor",
    "linked_viewer",
    "email_editor",
    "unlinked",
    "guest",
    "external",
    "non_member",
)


async def _sender(
    session: AsyncSession, org: _Org, state: str
) -> ChannelSenderEvidence:
    provider_user_id = f"U{uuid.uuid4().hex[:8]}"
    match state:
        case "linked_editor" | "linked_viewer":
            role = (
                "workspace-editor" if state == "linked_editor" else "workspace-viewer"
            )
            user = await _user(session, org, workspace_role=role)
            await _link(session, org, provider_user_id, user)
            return _evidence(provider_user_id)
        case "email_editor":
            user = await _user(session, org)
            return _evidence(provider_user_id, user.email)
        case "guest" | "external":
            user = await _user(session, org)
            return _evidence(
                provider_user_id,
                user.email,
                is_guest=state == "guest",
                is_external=state == "external",
            )
        case "non_member":
            user = await _user(session, org, member=False)
            await _link(session, org, provider_user_id, user)
            return _evidence(provider_user_id, user.email)
        case _:
            return _evidence(provider_user_id)


OK, LINK, DENY = None, ChannelLinkRequired, ChannelApproverNotAllowed
_P, _A = ChannelAccessPolicy, ChannelAction
# Columns follow SENDER_STATES.
EXPECTED_ROWS: dict[
    tuple[ChannelAccessPolicy, ChannelAction], tuple[type[Exception] | None, ...]
] = {
    (_P.OPEN, _A.MESSAGE): (OK, OK, OK, OK, OK, OK, OK),
    (_P.OPEN, _A.STOP): (OK, OK, OK, OK, OK, OK, OK),
    (_P.OPEN, _A.APPROVE): (OK, OK, OK, OK, OK, OK, OK),
    (_P.LINKED_APPROVALS, _A.MESSAGE): (OK, OK, OK, OK, OK, OK, OK),
    (_P.LINKED_APPROVALS, _A.STOP): (OK, OK, OK, OK, OK, OK, OK),
    (_P.LINKED_APPROVALS, _A.APPROVE): (OK, DENY, OK, LINK, LINK, LINK, LINK),
    (_P.LINKED_ONLY, _A.MESSAGE): (OK, OK, OK, LINK, LINK, LINK, LINK),
    (_P.LINKED_ONLY, _A.STOP): (OK, OK, OK, LINK, LINK, LINK, LINK),
    (_P.LINKED_ONLY, _A.APPROVE): (OK, DENY, OK, LINK, LINK, LINK, LINK),
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
    evidence = await _sender(session, org, state)
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, evidence=evidence
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


async def test_email_match_stores_an_email_link(
    session: AsyncSession, org: _Org
) -> None:
    user = await _user(session, org)
    actor = await resolve_channel_actor(
        session,
        organization_id=org.organization_id,
        evidence=_evidence("U1", user.email.upper()),
    )
    assert actor.user_id == user.id and actor.email == user.email
    link = await session.scalar(
        select(AgentChannelUser).where(AgentChannelUser.provider_user_id == "U1")
    )
    assert link is not None and link.link_method == ChannelLinkMethod.EMAIL


@pytest.mark.parametrize(
    "case",
    ["unconfirmed", "unverified_domain", "inactive_domain", "foreign_domain", "left"],
)
async def test_email_match_refuses_unqualified_evidence(
    session: AsyncSession, org: _Org, case: str
) -> None:
    domain = org.domain
    if case == "unverified_domain":
        domain = await _add_domain(
            session, org.organization_id, verification_method="dns"
        )
    elif case == "inactive_domain":
        domain = await _add_domain(session, org.organization_id, is_active=False)
    elif case == "foreign_domain":
        domain = f"{uuid.uuid4().hex[:12]}.example.org"
    user = await _user(session, org, member=case != "left", domain=domain)
    evidence = _evidence("U2", user.email, email_confirmed=case != "unconfirmed")
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, evidence=evidence
    )
    assert actor.user_id is None


async def test_email_link_stops_resolving_when_evidence_no_longer_qualifies(
    session: AsyncSession, org: _Org
) -> None:
    user = await _user(session, org)
    await _link(session, org, "U3", user, ChannelLinkMethod.EMAIL)
    actor = await resolve_channel_actor(
        session,
        organization_id=org.organization_id,
        evidence=_evidence("U3", user.email, is_guest=True),
    )
    assert actor.user_id is None
    link = await session.scalar(
        select(AgentChannelUser).where(AgentChannelUser.provider_user_id == "U3")
    )
    assert link is not None and link.user_id == user.id


async def test_email_never_replaces_a_challenge_link(
    session: AsyncSession, org: _Org
) -> None:
    linked = await _user(session, org)
    other = await _user(session, org)
    await _link(session, org, "U4", linked)
    actor = await resolve_channel_actor(
        session,
        organization_id=org.organization_id,
        evidence=_evidence("U4", other.email),
    )
    assert actor.user_id == linked.id


async def test_same_provider_user_is_isolated_per_organization(
    session: AsyncSession, org: _Org, svc_role: Role
) -> None:
    other_org_row = Organization(
        name="Other", slug=f"other-{uuid.uuid4().hex[:8]}", is_active=True
    )
    session.add(other_org_row)
    await session.commit()
    other = _Org(
        other_org_row.id,
        org.workspace_id,
        await _add_domain(session, other_org_row.id),
    )
    user_a = await _user(session, org)
    user_b = await _user(session, other, workspace_role=None)
    await _link(session, org, "U5", user_a)

    in_b = await resolve_channel_actor(
        session, organization_id=other.organization_id, evidence=_evidence("U5")
    )
    assert in_b.user_id is None
    await _link(session, other, "U5", user_b)
    in_a = await resolve_channel_actor(
        session, organization_id=org.organization_id, evidence=_evidence("U5")
    )
    in_b = await resolve_channel_actor(
        session, organization_id=other.organization_id, evidence=_evidence("U5")
    )
    assert (in_a.user_id, in_b.user_id) == (user_a.id, user_b.id)


class _SignedInAs:
    """The installed bot admin's provider sign-in, answering with one identity."""

    def __init__(self, identity: ChannelIdentity) -> None:
        self.identity = identity

    async def verify_provider_sign_in(self, *args: object, **kwargs: object):
        return self.identity


def _sign_in(org: _Org, provider_user_id: str, user: User) -> str:
    token = mint_link_token(
        organization_id=org.organization_id,
        workspace_id=org.workspace_id,
        bot_id=uuid.uuid4(),
        identity=ChannelIdentity(PROVIDER, TENANT, provider_user_id),
        interaction_id="interaction",
    )
    return mint_sign_in_state(link_token=token, user_id=user.id)


async def test_challenge_links_once_and_replaces_an_email_link(
    session: AsyncSession, org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _entitle(session, org.organization_id, monkeypatch)
    email_user = await _user(session, org)
    user = await _user(session, org)
    await _link(session, org, "U6", email_user, ChannelLinkMethod.EMAIL)
    admin = _SignedInAs(ChannelIdentity(PROVIDER, TENANT, "U6"))
    monkeypatch.setattr(
        channel_users_service, "require_channel_bot_admin", lambda: admin
    )
    sign_in = read_sign_in_state(_sign_in(org, "U6", user))

    await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    actor = await resolve_channel_actor(
        session, organization_id=org.organization_id, evidence=_evidence("U6")
    )
    assert actor.user_id == user.id
    with pytest.raises(ChannelLinkInvalid, match="already used"):
        await complete_channel_link(session, sign_in=sign_in, code="c", user_id=user.id)
    # A used link never reaches the provider again.
    with pytest.raises(ChannelLinkInvalid, match="already used"):
        await read_link_details(session, claims=sign_in.claims, user_id=user.id)
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
        session, organization_id=org.organization_id, evidence=_evidence("U11")
    )
    assert actor.user_id == user.id


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
    # A still-qualifying email does not relink an unlinked sender.
    actor = await resolve_channel_actor(
        session,
        organization_id=org.organization_id,
        evidence=_evidence("U9", user.email),
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
        {"result": ["error"], "error": ["cancelled"]},
    )
    assert _redirect_query(signed_out)[1] == {
        "result": ["error"],
        "error": ["signed_out"],
    }
    assert _redirect_query(unreadable) == (
        "/channel-links/invalid",
        {"result": ["error"], "error": ["invalid"]},
    )


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
