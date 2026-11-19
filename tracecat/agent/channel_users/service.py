"""Resolve channel senders to Tracecat users and decide what they may do.

A link is identity evidence only: membership and scopes are read live on every check.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots.provider import require_channel_bot_admin
from tracecat.agent.channel_users.enums import (
    ChannelAccessPolicy,
    ChannelAction,
    ChannelLinkError,
    ChannelLinkMethod,
)
from tracecat.agent.channel_users.schemas import (
    AgentChannelUserRead,
    ChannelLinkDetails,
    channel_link_callback_url,
    channel_link_page_url,
)
from tracecat.agent.channel_users.tokens import (
    ChannelLinkClaims,
    ChannelLinkInvalid,
    ChannelLinkSignIn,
    mint_link_token,
    mint_sign_in_state,
    read_link_token,
)
from tracecat.agent.channel_users.types import (
    ChannelActor,
    ChannelIdentity,
    ChannelSenderEvidence,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.authz.service import query_effective_scopes
from tracecat.db.models import (
    AgentChannelUser,
    Organization,
    OrganizationDomain,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.organization.domains import normalize_domain
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.redis.client import get_redis_client
from tracecat.tiers.access import require_org_entitlement
from tracecat.tiers.enums import Entitlement

APPROVE_SCOPE = "agent:execute"
# fastapi-users types User's columns as plain values; the table keeps SQL types.
_USERS = User.__table__


class ChannelLinkRequired(TracecatAuthorizationError):
    """The agent's policy needs a linked org member and the sender is not one."""


class ChannelApproverNotAllowed(TracecatAuthorizationError):
    """The linked sender may not approve this workspace's agent tool calls."""


def _is_current_member(organization_id: OrganizationID) -> ColumnElement[bool]:
    return and_(
        _USERS.c.is_active.is_(True),
        exists().where(
            OrganizationMembership.user_id == _USERS.c.id,
            OrganizationMembership.organization_id == organization_id,
        ),
    )


async def _current_member(
    session: AsyncSession, organization_id: OrganizationID, user_id: uuid.UUID
) -> User | None:
    return await session.scalar(
        select(User).where(_USERS.c.id == user_id, _is_current_member(organization_id))
    )


async def _upsert_link(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    identity: ChannelIdentity,
    user_id: uuid.UUID,
    link_method: ChannelLinkMethod,
    where: ColumnElement[bool] | None = None,
) -> uuid.UUID | None:
    """Link `identity` to `user_id`; None when `where` keeps the existing row."""
    statement = pg_insert(AgentChannelUser).values(
        organization_id=organization_id,
        provider=identity.provider,
        provider_tenant_id=identity.provider_tenant_id,
        provider_user_id=identity.provider_user_id,
        user_id=user_id,
        link_method=link_method,
    )
    return await session.scalar(
        statement.on_conflict_do_update(
            constraint="uq_agent_channel_user_org_provider_identity",
            set_={
                "user_id": user_id,
                "link_method": link_method,
                "linked_at": func.now(),
                "updated_at": func.now(),
            },
            where=where,
        ).returning(AgentChannelUser.id)
    )


def _actor(user: User) -> ChannelActor:
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return ChannelActor(user_id=user.id, name=full_name or user.email, email=user.email)


def _identity_matches(identity: ChannelIdentity) -> ColumnElement[bool]:
    return and_(
        AgentChannelUser.provider == identity.provider,
        AgentChannelUser.provider_tenant_id == identity.provider_tenant_id,
        AgentChannelUser.provider_user_id == identity.provider_user_id,
    )


async def _email_member(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    evidence: ChannelSenderEvidence,
) -> User | None:
    """The current member owning a qualifying sender email on a verified org domain."""
    if (
        evidence.email is None
        or not evidence.email_confirmed
        or evidence.is_guest
        or evidence.is_external
    ):
        return None
    _, at, domain = evidence.email.rpartition("@")
    if not at:
        return None
    try:
        normalized_domain = normalize_domain(domain).normalized_domain
    except ValueError:
        return None
    verified_domain = exists().where(
        OrganizationDomain.organization_id == organization_id,
        OrganizationDomain.normalized_domain == normalized_domain,
        OrganizationDomain.is_active.is_(True),
        # Platform-admin assignment is the verification; other methods need proof.
        or_(
            OrganizationDomain.verified_at.is_not(None),
            OrganizationDomain.verification_method == "platform_admin",
        ),
    )
    return await session.scalar(
        select(User).where(
            func.lower(_USERS.c.email) == evidence.email.lower(),
            _is_current_member(organization_id),
            verified_domain,
        )
    )


async def resolve_channel_actor(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    evidence: ChannelSenderEvidence,
) -> ChannelActor:
    """Resolve a sender to a current org member: stored challenge link first, else email.

    An email match is stored as an email link; an email link whose evidence no longer
    qualifies resolves to no user and keeps its row.
    Storing an email link commits `session`; pass one with no pending writes.
    """
    unlinked = ChannelActor(user_id=None, name=None, email=None)
    link = await session.scalar(
        select(AgentChannelUser).where(
            AgentChannelUser.organization_id == organization_id,
            _identity_matches(evidence),
        )
    )
    if link is not None:
        if link.user_id is None:
            # Unlinked or its user deleted: only a new challenge links it again.
            return unlinked
        if link.link_method == ChannelLinkMethod.CHALLENGE:
            user = await _current_member(session, organization_id, link.user_id)
            return unlinked if user is None else _actor(user)

    user = await _email_member(
        session, organization_id=organization_id, evidence=evidence
    )
    if user is None:
        return unlinked
    if link is not None and link.user_id == user.id:
        return _actor(user)
    stored = await _upsert_link(
        session,
        organization_id=organization_id,
        identity=evidence,
        user_id=user.id,
        link_method=ChannelLinkMethod.EMAIL,
        # Never replaces a challenge link, nor revives an unlinked row.
        where=and_(
            AgentChannelUser.link_method == ChannelLinkMethod.EMAIL,
            AgentChannelUser.user_id.is_not(None),
        ),
    )
    await session.commit()
    # A concurrent challenge or unlink won the row; resolve again next event.
    return unlinked if stored is None else _actor(user)


async def check_channel_access(
    session: AsyncSession,
    *,
    policy: ChannelAccessPolicy,
    action: ChannelAction,
    actor: ChannelActor,
    workspace_id: WorkspaceID,
) -> None:
    """Refuse a channel action the agent's policy does not allow this actor.

    Raises:
        ChannelLinkRequired: If the policy needs a linked member and there is none.
        ChannelApproverNotAllowed: If a linked member lacks `agent:execute` to approve.
    """
    if policy == ChannelAccessPolicy.OPEN:
        return
    if (
        policy == ChannelAccessPolicy.LINKED_APPROVALS
        and action != ChannelAction.APPROVE
    ):
        return
    if actor.user_id is None:
        raise ChannelLinkRequired(
            "Sign in with Tracecat to use this agent from the channel."
        )
    if action != ChannelAction.APPROVE:
        return
    organization_id = await session.scalar(
        select(Workspace.organization_id).where(Workspace.id == workspace_id)
    )
    if organization_id is None:
        raise ChannelApproverNotAllowed("This agent's workspace no longer exists.")
    scopes = await query_effective_scopes(
        session, actor.user_id, organization_id, workspace_id
    )
    if not has_scope(scopes, APPROVE_SCOPE):
        raise ChannelApproverNotAllowed(
            "You need permission to run agents in this workspace to approve "
            "its tool calls."
        )


def issue_link_challenge(
    *,
    organization_id: OrganizationID,
    workspace_id: WorkspaceID,
    bot_id: uuid.UUID,
    identity: ChannelIdentity,
    interaction_id: str,
) -> str:
    """Return the Tracecat page that links `identity`; valid once, for 10 minutes."""
    token = mint_link_token(
        organization_id=organization_id,
        workspace_id=workspace_id,
        bot_id=bot_id,
        identity=identity,
        interaction_id=interaction_id,
    )
    return channel_link_page_url(token)


async def _ensure_member(
    session: AsyncSession, *, organization_id: OrganizationID, user_id: uuid.UUID
) -> User:
    if (user := await _current_member(session, organization_id, user_id)) is None:
        raise ChannelLinkInvalid(
            "Sign in as a member of this organization to link",
            error=ChannelLinkError.NOT_MEMBER,
        )
    return user


def _used_key(claims: ChannelLinkClaims) -> str:
    return f"agent-channel-link:{claims.jti}"


async def _ensure_unused(claims: ChannelLinkClaims) -> None:
    redis = await get_redis_client()
    if await redis.exists(_used_key(claims)):
        raise ChannelLinkInvalid("This link was already used")


async def _ensure_linkable(
    session: AsyncSession, *, claims: ChannelLinkClaims, user_id: uuid.UUID
) -> User:
    """The confirming member, once the org may use channels and the link is unused."""
    user = await _ensure_member(
        session, organization_id=claims.organization_id, user_id=user_id
    )
    await require_org_entitlement(
        session, claims.organization_id, Entitlement.AGENT_CHANNELS_PREVIEW
    )
    await _ensure_unused(claims)
    return user


def _bot_service_role(claims: ChannelLinkClaims) -> Role:
    # Confirming needs org membership only, so the bot is read as the API service.
    return Role(
        type="service",
        service_id="tracecat-api",
        organization_id=claims.organization_id,
        workspace_id=claims.workspace_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )


async def read_link_details(
    session: AsyncSession, *, claims: ChannelLinkClaims, user_id: uuid.UUID
) -> ChannelLinkDetails:
    """What the confirm page shows; only to a current member of the token's org."""
    user = await _ensure_linkable(session, claims=claims, user_id=user_id)
    organization_name = await session.scalar(
        select(Organization.name).where(Organization.id == claims.organization_id)
    )
    return ChannelLinkDetails(
        provider=claims.identity.provider,
        provider_tenant_id=claims.identity.provider_tenant_id,
        provider_user_id=claims.identity.provider_user_id,
        organization_name=organization_name or "",
        user_email=user.email,
        expires_at=datetime.fromtimestamp(claims.exp, UTC),
    )


async def start_provider_sign_in(
    session: AsyncSession, *, link_token: str, user_id: uuid.UUID
) -> str:
    """Return the provider sign-in URL; its state binds the token to `user_id`."""
    claims = read_link_token(link_token)
    await _ensure_linkable(session, claims=claims, user_id=user_id)
    return await require_channel_bot_admin().provider_sign_in_url(
        session,
        _bot_service_role(claims),
        claims.bot_id,
        state=mint_sign_in_state(link_token=link_token, user_id=user_id),
        redirect_uri=channel_link_callback_url(),
    )


async def complete_channel_link(
    session: AsyncSession,
    *,
    sign_in: ChannelLinkSignIn,
    code: str,
    user_id: uuid.UUID,
) -> None:
    """Link the provider user who signed in to `user_id`, replacing any email link.

    Raises:
        ChannelLinkInvalid: If another user confirmed, the user left the org, the
            token was used, or the provider user differs from the token's.
    """
    claims = sign_in.claims
    if sign_in.user_id != user_id:
        raise ChannelLinkInvalid(
            "This link was confirmed by another Tracecat user",
            error=ChannelLinkError.OTHER_USER,
        )
    await _ensure_member(
        session, organization_id=claims.organization_id, user_id=user_id
    )
    await require_org_entitlement(
        session, claims.organization_id, Entitlement.AGENT_CHANNELS_PREVIEW
    )
    await _ensure_unused(claims)
    identity = await require_channel_bot_admin().verify_provider_sign_in(
        session,
        _bot_service_role(claims),
        claims.bot_id,
        code=code,
        redirect_uri=channel_link_callback_url(),
    )
    if identity != claims.identity:
        raise ChannelLinkInvalid(
            "You signed in as a different provider user",
            error=ChannelLinkError.WRONG_ACCOUNT,
        )
    # Claimed only after a matching sign-in, so a failed attempt can be retried.
    ttl = max(1, claims.exp - int(datetime.now(UTC).timestamp()))
    redis = await get_redis_client()
    if not await redis.set_if_not_exists(_used_key(claims), "1", expire_seconds=ttl):
        raise ChannelLinkInvalid("This link was already used")
    await _upsert_link(
        session,
        organization_id=claims.organization_id,
        identity=identity,
        user_id=user_id,
        link_method=ChannelLinkMethod.CHALLENGE,
    )
    await session.commit()


async def unlink_channel_user(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    link_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> bool:
    """Unlink, only `user_id`'s own link when given; past actions keep their actor.

    The row stays with no user so an email match cannot relink it; a challenge can.
    """
    statement = (
        update(AgentChannelUser)
        .where(
            AgentChannelUser.id == link_id,
            AgentChannelUser.organization_id == organization_id,
            AgentChannelUser.user_id.is_not(None),
        )
        .values(user_id=None, updated_at=func.now())
    )
    if user_id is not None:
        statement = statement.where(AgentChannelUser.user_id == user_id)
    unlinked = await session.scalar(statement.returning(AgentChannelUser.id))
    await session.commit()
    return unlinked is not None


async def list_channel_users(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    user_id: uuid.UUID | None,
    page: PageParams,
) -> CursorPaginatedResponse[AgentChannelUserRead]:
    """A page of the org's links, only `user_id`'s when given; newest first."""
    statement = select(AgentChannelUser).where(
        AgentChannelUser.organization_id == organization_id,
        AgentChannelUser.user_id.is_not(None),
    )
    if user_id is not None:
        statement = statement.where(AgentChannelUser.user_id == user_id)
    result = await paginate(
        session,
        statement,
        page=page,
        order_by=(AgentChannelUser.created_at.desc(), AgentChannelUser.id.desc()),
    )
    return CursorPaginatedResponse(
        items=[AgentChannelUserRead.model_validate(link) for link in result.items],
        next_cursor=result.next_cursor,
        prev_cursor=result.prev_cursor,
        has_more=result.has_more,
        has_previous=result.has_previous,
    )
