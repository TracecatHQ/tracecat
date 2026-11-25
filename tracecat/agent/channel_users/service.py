"""Resolve channel senders to Tracecat users and decide what they may do.

A link is identity evidence only: membership and scopes are read live on every check.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, delete, exists, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_bots.provider import require_channel_bot_admin
from tracecat.agent.channel_bots.schemas import ChannelBotAppOwner, ChannelBotStatus
from tracecat.agent.channel_users.enums import (
    ChannelAccessPolicy,
    ChannelAction,
    ChannelLinkError,
)
from tracecat.agent.channel_users.schemas import (
    AgentChannelUserRead,
    channel_link_callback_url,
    channel_link_page_url,
)
from tracecat.agent.channel_users.tokens import (
    ChannelLinkClaims,
    ChannelLinkInvalid,
    ChannelLinkSignIn,
    ChannelSettingsSignIn,
    mint_link_token,
    mint_settings_sign_in_state,
    mint_sign_in_state,
    read_link_token,
)
from tracecat.agent.channel_users.types import ChannelActor, ChannelIdentity
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.authz.service import query_effective_scopes
from tracecat.db.engine import (
    get_async_session_bypass_rls_context_manager,
    get_async_session_tenant_context_manager,
)
from tracecat.db.models import (
    AgentChannelBot,
    AgentChannelUser,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatValidationError
from tracecat.identifiers import OrganizationID, WorkspaceID
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
    provider_email: str | None,
) -> None:
    """Link `identity` to `user_id`, replacing any earlier link of it."""
    statement = pg_insert(AgentChannelUser).values(
        organization_id=organization_id,
        provider=identity.provider,
        provider_tenant_id=identity.provider_tenant_id,
        provider_user_id=identity.provider_user_id,
        user_id=user_id,
        provider_email=provider_email,
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_agent_channel_user_org_provider_identity",
            set_={
                "user_id": user_id,
                "provider_email": provider_email,
                "updated_at": func.now(),
            },
        )
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


async def resolve_channel_actor(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    identity: ChannelIdentity,
) -> ChannelActor:
    """Resolve a sender to the current org member it is linked to, else unlinked."""
    user = await session.scalar(
        select(User)
        .join(AgentChannelUser, AgentChannelUser.user_id == _USERS.c.id)
        .where(
            AgentChannelUser.organization_id == organization_id,
            _identity_matches(identity),
            _is_current_member(organization_id),
        )
    )
    if user is None:
        return ChannelActor(user_id=None, name=None, email=None)
    return _actor(user)


async def linked_members(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    identities: Collection[ChannelIdentity],
) -> dict[ChannelIdentity, User]:
    """The current org members these identities are linked to."""
    if not identities:
        return {}
    rows = await session.execute(
        select(
            AgentChannelUser.provider,
            AgentChannelUser.provider_tenant_id,
            AgentChannelUser.provider_user_id,
            User,
        )
        .join(User, _USERS.c.id == AgentChannelUser.user_id)
        .where(
            AgentChannelUser.organization_id == organization_id,
            tuple_(
                AgentChannelUser.provider,
                AgentChannelUser.provider_tenant_id,
                AgentChannelUser.provider_user_id,
            ).in_(
                [
                    (i.provider, i.provider_tenant_id, i.provider_user_id)
                    for i in identities
                ]
            ),
            _is_current_member(organization_id),
        )
    )
    return {
        ChannelIdentity(provider, tenant_id, user_id): user
        for provider, tenant_id, user_id, user in rows.tuples()
    }


async def linked_member_names(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    identities: Collection[ChannelIdentity],
) -> dict[ChannelIdentity, str]:
    """Display names of the current org members these identities are linked to."""
    members = await linked_members(
        session, organization_id=organization_id, identities=identities
    )
    return {
        identity: name
        for identity, user in members.items()
        if (name := _actor(user).name)
    }


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


async def _ensure_channels_member(
    session: AsyncSession, *, organization_id: OrganizationID, user_id: uuid.UUID
) -> User:
    """The member, once the org may use channels."""
    user = await _ensure_member(
        session, organization_id=organization_id, user_id=user_id
    )
    await require_org_entitlement(
        session, organization_id, Entitlement.AGENT_CHANNELS_PREVIEW
    )
    return user


def _ensure_started_by(
    sign_in: ChannelLinkSignIn | ChannelSettingsSignIn, user_id: uuid.UUID
) -> None:
    if sign_in.user_id != user_id:
        raise ChannelLinkInvalid(
            "This sign-in was started by another Tracecat user",
            error=ChannelLinkError.OTHER_USER,
        )


async def _ensure_linkable(
    session: AsyncSession, *, claims: ChannelLinkClaims, user_id: uuid.UUID
) -> User:
    """The confirming member, once the org may use channels and the link is unused."""
    user = await _ensure_channels_member(
        session, organization_id=claims.organization_id, user_id=user_id
    )
    await _ensure_unused(claims)
    return user


def _bot_service_role(
    organization_id: OrganizationID, workspace_id: WorkspaceID
) -> Role:
    # Confirming needs org membership only, so the bot is read as the API service.
    return Role(
        type="service",
        service_id="tracecat-api",
        organization_id=organization_id,
        workspace_id=workspace_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )


async def start_provider_sign_in(
    session: AsyncSession, *, link_token: str, user_id: uuid.UUID
) -> str:
    """Return the provider sign-in URL; its state binds the token to `user_id`."""
    claims = read_link_token(link_token)
    await _ensure_linkable(session, claims=claims, user_id=user_id)
    return await require_channel_bot_admin().provider_sign_in_url(
        session,
        _bot_service_role(claims.organization_id, claims.workspace_id),
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
    """Link the provider user who signed in to `user_id`, replacing any earlier link.

    Raises:
        ChannelLinkInvalid: If another user confirmed, the user left the org, the
            token was used, or the provider user differs from the token's.
    """
    claims = sign_in.claims
    _ensure_started_by(sign_in, user_id)
    await _ensure_linkable(session, claims=claims, user_id=user_id)
    signed_in = await require_channel_bot_admin().verify_provider_sign_in(
        session,
        _bot_service_role(claims.organization_id, claims.workspace_id),
        claims.bot_id,
        code=code,
        redirect_uri=channel_link_callback_url(),
    )
    if signed_in.identity != claims.identity:
        raise ChannelLinkInvalid(
            "You signed in as a different provider user",
            error=ChannelLinkError.WRONG_ACCOUNT,
        )
    # Claimed only after a matching sign-in, so a failed attempt can be retried.
    ttl = max(1, claims.exp - int(datetime.now(UTC).timestamp()))
    redis = await get_redis_client()
    if not await redis.set_if_not_exists(_used_key(claims), "1", expire_seconds=ttl):
        raise ChannelLinkInvalid("This link was already used")
    try:
        await _upsert_link(
            session,
            organization_id=claims.organization_id,
            identity=signed_in.identity,
            user_id=user_id,
            provider_email=signed_in.email,
        )
        await session.commit()
    except BaseException:
        # The link was not stored (or the request was cancelled); a retry must find it unused.
        await redis.delete(_used_key(claims))
        raise


def _active_org_bots(organization_id: OrganizationID) -> ColumnElement[bool]:
    return and_(
        AgentChannelBot.status == ChannelBotStatus.ACTIVE,
        AgentChannelBot.provider_tenant_id.is_not(None),
        AgentChannelBot.workspace_id.in_(
            select(Workspace.id).where(Workspace.organization_id == organization_id)
        ),
    )


async def settings_sign_in_bot(
    organization_id: OrganizationID,
) -> AgentChannelBot | None:
    """The active bot whose app signs in from Settings; a platform app first, since it
    signs in any tenant."""
    # Bots are workspace rows; the org filter is explicit.
    async with get_async_session_bypass_rls_context_manager() as session:
        return await session.scalar(
            select(AgentChannelBot)
            .where(_active_org_bots(organization_id))
            .order_by(
                (AgentChannelBot.app_owner == ChannelBotAppOwner.PLATFORM).desc(),
                AgentChannelBot.created_at.desc(),
            )
            .limit(1)
        )


async def start_settings_sign_in(
    *, organization_id: OrganizationID, user_id: uuid.UUID
) -> str:
    """Return a provider sign-in URL for linking from Settings, with no link token.

    Raises:
        TracecatValidationError: If the org has no active bot to sign in through.
        ChannelLinkInvalid: If the user is not a member.
    """
    if (bot := await settings_sign_in_bot(organization_id)) is None:
        raise TracecatValidationError(
            "Add a Slack bot to a workspace before linking a Slack account"
        )
    async with get_async_session_tenant_context_manager(
        workspace_id=bot.workspace_id
    ) as session:
        await _ensure_channels_member(
            session, organization_id=organization_id, user_id=user_id
        )
        return await require_channel_bot_admin().provider_sign_in_url(
            session,
            _bot_service_role(organization_id, bot.workspace_id),
            bot.id,
            state=mint_settings_sign_in_state(
                organization_id=organization_id,
                workspace_id=bot.workspace_id,
                bot_id=bot.id,
                user_id=user_id,
            ),
            redirect_uri=channel_link_callback_url(),
        )


async def _has_active_bot(
    organization_id: OrganizationID, identity: ChannelIdentity
) -> bool:
    async with get_async_session_bypass_rls_context_manager() as bots_session:
        return bool(
            await bots_session.scalar(
                select(
                    exists().where(
                        _active_org_bots(organization_id),
                        AgentChannelBot.provider == identity.provider,
                        AgentChannelBot.provider_tenant_id
                        == identity.provider_tenant_id,
                    )
                )
            )
        )


async def complete_settings_link(
    session: AsyncSession,
    *,
    sign_in: ChannelSettingsSignIn,
    code: str,
    user_id: uuid.UUID,
) -> None:
    """Link the provider user who signed in from Settings to `user_id`.

    Raises:
        ChannelLinkInvalid: If another user started the sign-in, the user left the
            org, or the signed-in tenant has no active bot in the org.
    """
    _ensure_started_by(sign_in, user_id)
    organization_id = sign_in.organization_id
    await _ensure_channels_member(
        session, organization_id=organization_id, user_id=user_id
    )
    signed_in = await require_channel_bot_admin().verify_provider_sign_in(
        session,
        _bot_service_role(organization_id, sign_in.workspace_id),
        sign_in.bot_id,
        code=code,
        redirect_uri=channel_link_callback_url(),
    )
    if not await _has_active_bot(organization_id, signed_in.identity):
        raise ChannelLinkInvalid(
            "Your organization has no active bot in that provider tenant",
            error=ChannelLinkError.NO_BOT,
        )
    await _upsert_link(
        session,
        organization_id=organization_id,
        identity=signed_in.identity,
        user_id=user_id,
        provider_email=signed_in.email,
    )
    await session.commit()


async def complete_sign_in(
    session: AsyncSession,
    *,
    sign_in: ChannelLinkSignIn | ChannelSettingsSignIn,
    code: str,
    user_id: uuid.UUID,
) -> None:
    """Link the provider user who signed in, by the flow that started the sign-in."""
    match sign_in:
        case ChannelSettingsSignIn():
            await complete_settings_link(
                session, sign_in=sign_in, code=code, user_id=user_id
            )
        case ChannelLinkSignIn():
            await complete_channel_link(
                session, sign_in=sign_in, code=code, user_id=user_id
            )


async def unlink_channel_user(
    session: AsyncSession,
    *,
    organization_id: OrganizationID,
    link_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> bool:
    """Delete a link, only `user_id`'s own when given; past actions keep their actor."""
    statement = delete(AgentChannelUser).where(
        AgentChannelUser.id == link_id,
        AgentChannelUser.organization_id == organization_id,
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
        AgentChannelUser.organization_id == organization_id
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
