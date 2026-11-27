"""Link channel senders to Tracecat users, and list or remove those links."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from tracecat.agent.channel_users.enums import ChannelLinkError
from tracecat.agent.channel_users.schemas import (
    CHANNEL_LINK_CALLBACK_ROUTE,
    CHANNEL_LINKS_PREFIX,
    AgentChannelUserRead,
    ChannelLinkSignInRead,
    channel_link_page_url,
    channel_link_settings_url,
)
from tracecat.agent.channel_users.service import (
    complete_sign_in,
    list_channel_users,
    start_provider_sign_in,
    start_settings_sign_in,
    unlink_channel_user,
)
from tracecat.agent.channel_users.tokens import (
    ChannelLinkInvalid,
    ChannelLinkSignIn,
    ChannelSettingsSignIn,
    is_settings_sign_in_state,
    read_link_token,
    read_settings_sign_in_state,
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


def _settings_page(error: ChannelLinkError | None) -> RedirectResponse:
    query = (
        {"result": "linked"} if error is None else {"result": "error", "error": error}
    )
    return RedirectResponse(f"{channel_link_settings_url()}&{urlencode(query)}")


async def _link_signed_in_user(
    sign_in: ChannelLinkSignIn | ChannelSettingsSignIn,
    *,
    user: User | None,
    code: str | None,
    provider_error: str | None,
) -> ChannelLinkError | None:
    if provider_error is not None or code is None:
        return ChannelLinkError.CANCELLED
    if user is None:
        return ChannelLinkError.SIGNED_OUT
    try:
        # These routes take the user, not an org role, so the org check runs here.
        try:
            await enforce_org_ip_allowlist(
                sign_in.organization_id, bypass=user.is_superuser
            )
        except HTTPException:
            return ChannelLinkError.BLOCKED_NETWORK
        async with get_async_session_tenant_context_manager(
            workspace_id=sign_in.workspace_id
        ) as session:
            await complete_sign_in(session, sign_in=sign_in, code=code, user_id=user.id)
    except ChannelLinkInvalid as exc:
        return exc.error
    except (TracecatValidationError, EntitlementRequired):
        return ChannelLinkError.FAILED
    except Exception as exc:
        # The error can carry the sign-in code or provider response.
        logger.error("Channel link sign-in failed", error_type=type(exc).__name__)
        return ChannelLinkError.FAILED
    return None


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
    """The provider's return after sign-in: link, then back to where it started."""
    state = state or ""
    if is_settings_sign_in_state(state):
        try:
            settings_sign_in = read_settings_sign_in_state(state)
        except ChannelLinkInvalid as exc:
            return _settings_page(exc.error)
        return _settings_page(
            await _link_signed_in_user(
                settings_sign_in, user=user, code=code, provider_error=error
            )
        )
    try:
        sign_in = read_sign_in_state(state)
    except ChannelLinkInvalid as exc:
        return _link_page(None, exc.error)
    outcome = await _link_signed_in_user(
        sign_in, user=user, code=code, provider_error=error
    )
    return _link_page(sign_in, outcome)


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


@router.post("/me/sign-in", response_model=ChannelLinkSignInRead)
async def start_my_channel_link_sign_in(*, role: OrgUserRole) -> ChannelLinkSignInRead:
    """Start a provider sign-in from Settings that links the caller, with no link token."""
    if role.organization_id is None or role.user_id is None:
        raise _not_found()
    try:
        sign_in_url = await start_settings_sign_in(
            organization_id=role.organization_id, user_id=role.user_id
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
