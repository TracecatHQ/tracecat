"""Link channel senders to Tracecat users, and list or remove those links."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from tracecat.agent.channels.users.enums import ChannelLinkError
from tracecat.agent.channels.users.schemas import (
    CHANNEL_LINK_CALLBACK_ROUTE,
    CHANNEL_LINKS_PREFIX,
    AgentChannelUserRead,
    ChannelLinkSignInRead,
    channel_link_page_url,
)
from tracecat.agent.channels.users.service import (
    complete_channel_link,
    list_channel_users,
    start_provider_sign_in,
    unlink_channel_user,
)
from tracecat.agent.channels.users.tokens import (
    ChannelLinkInvalid,
    ChannelLinkSignIn,
    read_link_token,
    read_sign_in_state,
)
from tracecat.auth.credentials import OptionalUserDep
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.ip_allowlist_enforcement import enforce_org_ip_allowlist
from tracecat.auth.users import current_active_user
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_tenant_context_manager
from tracecat.db.models import User
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, PageParams
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement


async def _require_agent_channels_preview(
    role: OrgUserRole, session: AsyncDBSession
) -> None:
    await check_entitlement(session, role, Entitlement.AGENT_CHANNELS_PREVIEW)


# Link routes check the token's org entitlement in the service; the callback must redirect.
link_router = APIRouter(prefix=CHANNEL_LINKS_PREFIX, tags=["agent-channel-users"])
router = APIRouter(
    prefix="/agent/channel-users",
    tags=["agent-channel-users"],
    dependencies=[Depends(_require_agent_channels_preview)],
)

CurrentUser = Annotated[User, Depends(current_active_user)]
PageQuery = Annotated[PageParams, Depends()]


def _bad_request(exc: TracecatValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _link_page(
    sign_in: ChannelLinkSignIn | None, error: ChannelLinkError | None
) -> RedirectResponse:
    query: dict[str, str] = (
        {"result": "linked"} if error is None else {"result": "error", "error": error}
    )
    if sign_in is not None:
        identity = sign_in.claims.identity
        query |= {"provider": identity.provider, "tenant": identity.provider_tenant_id}
    # An unreadable state has no token; the page shows the error without fetching.
    page_url = channel_link_page_url(sign_in.link_token if sign_in else "invalid")
    return RedirectResponse(f"{page_url}?{urlencode(query)}")


@link_router.get(
    CHANNEL_LINK_CALLBACK_ROUTE.removeprefix(CHANNEL_LINKS_PREFIX),
    include_in_schema=False,
)
async def handle_channel_link_callback(
    *,
    user: OptionalUserDep,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """The provider's return after sign-in: link, then back to the link page."""
    try:
        sign_in = read_sign_in_state(state or "")
    except ChannelLinkInvalid as exc:
        return _link_page(None, exc.error)
    if error is not None or code is None:
        return _link_page(sign_in, ChannelLinkError.CANCELLED)
    if user is None:
        return _link_page(sign_in, ChannelLinkError.SIGNED_OUT)
    try:
        # These routes take the user, not an org role, so the org check runs here.
        try:
            await enforce_org_ip_allowlist(
                sign_in.claims.organization_id, bypass=user.is_superuser
            )
        except HTTPException:
            return _link_page(sign_in, ChannelLinkError.BLOCKED_NETWORK)
        async with get_async_session_tenant_context_manager(
            workspace_id=sign_in.claims.workspace_id
        ) as session:
            await complete_channel_link(
                session, sign_in=sign_in, code=code, user_id=user.id
            )
    except ChannelLinkInvalid as exc:
        return _link_page(sign_in, exc.error)
    except (TracecatValidationError, EntitlementRequired):
        return _link_page(sign_in, ChannelLinkError.FAILED)
    except Exception as exc:
        # The error can carry the sign-in code or provider response.
        logger.error("Channel link sign-in failed", error_type=type(exc).__name__)
        return _link_page(sign_in, ChannelLinkError.FAILED)
    return _link_page(sign_in, None)


@link_router.post("/{token}/sign-in", response_model=ChannelLinkSignInRead)
async def start_channel_link_sign_in(
    *, user: CurrentUser, token: str
) -> ChannelLinkSignInRead:
    """Start the provider sign-in that proves the sender is this user."""
    try:
        claims = read_link_token(token)
        await enforce_org_ip_allowlist(claims.organization_id, bypass=user.is_superuser)
        async with get_async_session_tenant_context_manager(
            workspace_id=claims.workspace_id
        ) as session:
            sign_in_url = await start_provider_sign_in(
                session, link_token=token, user_id=user.id
            )
    except TracecatValidationError as exc:
        raise _bad_request(exc) from exc
    return ChannelLinkSignInRead(sign_in_url=sign_in_url)


@router.get("/me", response_model=CursorPaginatedResponse[AgentChannelUserRead])
async def list_my_channel_users(
    *, role: OrgUserRole, session: AsyncDBSession, page: PageQuery
) -> CursorPaginatedResponse[AgentChannelUserRead]:
    """The caller's own channel links in this organization."""
    if role.organization_id is None or role.user_id is None:
        raise _not_found()
    return await list_channel_users(
        session, organization_id=role.organization_id, user_id=role.user_id, page=page
    )


@router.delete("/me/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_my_channel_user(
    *, role: OrgUserRole, session: AsyncDBSession, link_id: uuid.UUID
) -> None:
    """Remove one of the caller's own links."""
    if role.organization_id is None or role.user_id is None:
        raise _not_found()
    if not await unlink_channel_user(
        session,
        organization_id=role.organization_id,
        link_id=link_id,
        user_id=role.user_id,
    ):
        raise _not_found()


@router.get("", response_model=CursorPaginatedResponse[AgentChannelUserRead])
@require_scope("org:member:remove")
async def list_organization_channel_users(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    page: PageQuery,
    user_id: uuid.UUID | None = None,
) -> CursorPaginatedResponse[AgentChannelUserRead]:
    """Every channel link in the organization, or one member's."""
    if role.organization_id is None:
        raise _not_found()
    return await list_channel_users(
        session, organization_id=role.organization_id, user_id=user_id, page=page
    )


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:member:remove")
async def unlink_organization_channel_user(
    *, role: OrgUserRole, session: AsyncDBSession, link_id: uuid.UUID
) -> None:
    """Remove any link in the organization."""
    if role.organization_id is None:
        raise _not_found()
    if not await unlink_channel_user(
        session, organization_id=role.organization_id, link_id=link_id, user_id=None
    ):
        raise _not_found()
