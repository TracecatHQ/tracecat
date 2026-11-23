"""Channel bot admin routes; without the enterprise admin they answer "requires Enterprise".

`/agent/channels/...` keeps the legacy token API until ENG-2380.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from tracecat import config
from tracecat.agent.channel_bots.provider import (
    ChannelBotAdmin,
    ChannelListingNeedsReinstall,
    ChannelListingNotSupported,
    get_channel_bot_admin,
)
from tracecat.agent.channel_bots.schemas import (
    CHANNEL_BOTS_PREFIX,
    OAUTH_CALLBACK_ROUTE,
    AgentChannelBotCreate,
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    AgentChannelRouteRead,
    AgentChannelRouteSet,
    AgentChannelWorkspaceRoutesPage,
    ChannelBotAppOwner,
    ChannelBotChannel,
    ChannelBotInstall,
    ChannelBotInstallResult,
    ChannelBotOAuthCallback,
    ChannelBotPlatformApp,
    ChannelBotPlatformInstall,
    ChannelBotProvider,
)
from tracecat.auth.credentials import OptionalUserDep
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, PageParams, PaginationError
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement


def _require_admin() -> ChannelBotAdmin:
    if (admin := get_channel_bot_admin()) is None:
        raise EntitlementRequired(
            Entitlement.AGENT_CHANNELS.value,
            message="Channel bots require Tracecat Enterprise",
        )
    return admin


async def _require_agent_channels_preview(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> None:
    # `agent_channels` alone keeps the legacy channels; channel bots need only the preview.
    await check_entitlement(session, role, Entitlement.AGENT_CHANNELS_PREVIEW)


Admin = Annotated[ChannelBotAdmin, Depends(_require_admin)]
PageQuery = Annotated[PageParams, Depends()]

_CHANNEL_LISTING_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_424_FAILED_DEPENDENCY: {
        "description": "The bot's app lacks the scopes to list channels; reinstall it"
    },
    status.HTTP_501_NOT_IMPLEMENTED: {
        "description": "The bot's provider cannot list its channels"
    },
}

router = APIRouter(
    prefix=CHANNEL_BOTS_PREFIX,
    tags=["agent-channel-bots"],
    dependencies=[Depends(_require_agent_channels_preview)],
)
public_router = APIRouter(tags=["public"])


async def _call[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except TracecatNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TracecatConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChannelListingNotSupported as exc:
        # The UI falls back to a typed channel ID on this status.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ChannelListingNeedsReinstall as exc:
        # The UI asks for a reinstall on this status.
        raise HTTPException(status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc)) from exc
    except PaginationError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=exc.detail or str(exc)
        ) from exc
    except TracecatValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=CursorPaginatedResponse[AgentChannelBotRead])
@require_scope("agent:read")
async def list_bots(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
    agent_preset_id: uuid.UUID | None = Query(default=None),
    provider: ChannelBotProvider | None = Query(default=None),
    app_owner: ChannelBotAppOwner | None = Query(default=None),
    page: PageQuery,
) -> CursorPaginatedResponse[AgentChannelBotRead]:
    return await _call(
        admin.list_bots(
            session,
            role,
            agent_preset_id=agent_preset_id,
            provider=provider,
            app_owner=app_owner,
            page=page,
        )
    )


@router.post(
    "", response_model=AgentChannelBotRead, status_code=status.HTTP_201_CREATED
)
@require_scope("agent:update")
async def create_bot(
    *,
    params: AgentChannelBotCreate,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> AgentChannelBotRead:
    return await _call(admin.create_bot(session, role, params))


@router.patch("/{bot_id}", response_model=AgentChannelBotRead)
@require_scope("agent:update")
async def update_bot(
    *,
    bot_id: uuid.UUID,
    params: AgentChannelBotUpdate,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> AgentChannelBotRead:
    return await _call(admin.update_bot(session, role, bot_id, params))


@router.post("/{bot_id}/rotate", response_model=AgentChannelBotRead)
@require_scope("agent:update")
async def rotate_bot_endpoint(
    *,
    bot_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> AgentChannelBotRead:
    return await _call(admin.rotate_endpoint(session, role, bot_id))


@router.post("/{bot_id}/disconnect", response_model=AgentChannelBotRead)
@require_scope("agent:update")
async def disconnect_bot(
    *,
    bot_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> AgentChannelBotRead:
    return await _call(admin.disconnect_bot(session, role, bot_id))


@router.post("/install", response_model=ChannelBotInstallResult)
@require_scope("agent:update")
async def install_bot(
    *,
    params: Annotated[ChannelBotInstall, Body()],
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> ChannelBotInstallResult:
    return await _call(admin.install(session, role, params))


@router.get("/platform-apps", response_model=list[ChannelBotPlatformApp])
@require_scope("agent:read")
async def list_platform_apps(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> list[ChannelBotPlatformApp]:
    return await _call(admin.list_platform_apps(session, role))


@router.post("/platform-apps/install", response_model=ChannelBotInstallResult)
@require_scope("agent:update")
async def install_platform_bot(
    *,
    params: ChannelBotPlatformInstall,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> ChannelBotInstallResult:
    return await _call(admin.install_platform(session, role, params))


@router.get("/routes", response_model=AgentChannelWorkspaceRoutesPage)
@require_scope("agent:read")
async def list_workspace_routes(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
    q: Annotated[str | None, Query(max_length=255)] = None,
    page: PageQuery,
) -> AgentChannelWorkspaceRoutesPage:
    """Every channel the workspace's bots answer in, across bots, with its agent."""
    return await _call(admin.list_workspace_routes(session, role, q=q, page=page))


@router.get(
    "/{bot_id}/routes", response_model=CursorPaginatedResponse[AgentChannelRouteRead]
)
@require_scope("agent:read")
async def list_bot_routes(
    *,
    bot_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
    agent_preset_id: uuid.UUID | None = Query(default=None),
    page: PageQuery,
) -> CursorPaginatedResponse[AgentChannelRouteRead]:
    return await _call(
        admin.list_routes(
            session,
            role,
            bot_id,
            agent_preset_id=agent_preset_id,
            page=page,
        )
    )


@router.post(
    "/{bot_id}/routes",
    response_model=AgentChannelRouteRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("agent:update")
async def add_bot_route(
    *,
    bot_id: uuid.UUID,
    params: AgentChannelRouteSet,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> AgentChannelRouteRead:
    """Route a channel that has no route yet; 409 when it has one."""
    return await _call(admin.add_route(session, role, bot_id, params))


@router.get(
    "/{bot_id}/channels",
    response_model=CursorPaginatedResponse[ChannelBotChannel],
    responses=_CHANNEL_LISTING_RESPONSES,
)
@require_scope("agent:read")
async def list_bot_channels(
    *,
    bot_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
    page: PageQuery,
) -> CursorPaginatedResponse[ChannelBotChannel]:
    """The channels the bot is a member of, for an install of any app."""
    return await _call(admin.list_channels(session, role, bot_id, page=page))


@router.get(
    "/{bot_id}/routable-channels",
    response_model=CursorPaginatedResponse[ChannelBotChannel],
    responses=_CHANNEL_LISTING_RESPONSES,
)
@require_scope("agent:read")
async def list_bot_routable_channels(
    *,
    bot_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
    page: PageQuery,
) -> CursorPaginatedResponse[ChannelBotChannel]:
    return await _call(admin.list_routable_channels(session, role, bot_id, page=page))


@router.put("/{bot_id}/routes", response_model=AgentChannelRouteRead)
@require_scope("agent:update")
async def set_bot_route(
    *,
    bot_id: uuid.UUID,
    params: AgentChannelRouteSet,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> AgentChannelRouteRead:
    return await _call(admin.set_route(session, role, bot_id, params))


# The channel ID is a query parameter: a path segment cannot carry `/`, `?` or `#`.
@router.delete("/{bot_id}/routes", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def delete_bot_route(
    *,
    bot_id: uuid.UUID,
    provider_channel_id: Annotated[
        str, Query(min_length=1, max_length=255, pattern=r"^\S+$")
    ],
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> None:
    await _call(admin.delete_route(session, role, bot_id, provider_channel_id))


_WEB_SCHEMES = {"http", "https"}


def _is_usable_app_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        # Reading the port validates it; a netloc can be credentials or a bad port alone.
        parsed.port  # noqa: B018
    except ValueError:
        return False
    return parsed.scheme in _WEB_SCHEMES and bool(parsed.hostname)


def _redirect(base_url: str, *, ok: bool, message: str | None) -> Response:
    # Without a host the browser resolves the target against the callback itself.
    if not _is_usable_app_url(config.TRACECAT__PUBLIC_APP_URL):
        logger.error("Channel bot OAuth callback has no usable public app URL")
        return PlainTextResponse(
            "Tracecat's public app URL is not configured.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["channel_bot_install"] = "success" if ok else "error"
    if message:
        query["channel_bot_message"] = message
    return RedirectResponse(urlunparse(parsed._replace(query=urlencode(query))))


def _same_app_or_default(return_url: str | None) -> str:
    default_url = config.TRACECAT__PUBLIC_APP_URL
    if return_url is None:
        return default_url
    try:
        app_url, target = urlparse(default_url), urlparse(return_url)
    except ValueError:
        return default_url
    # A blank or hostless app URL must not let a hostless return URL match it.
    if app_url.scheme not in _WEB_SCHEMES or not app_url.netloc:
        return default_url
    # Schemes may differ: a TLS-terminating proxy serves an http app URL over https.
    if target.scheme not in _WEB_SCHEMES or target.netloc != app_url.netloc:
        return default_url
    return return_url


@public_router.get(OAUTH_CALLBACK_ROUTE, include_in_schema=False)
async def handle_bot_oauth_callback(
    *,
    provider: ChannelBotProvider,
    callback: Annotated[ChannelBotOAuthCallback, Query()],
    user: OptionalUserDep,
) -> Response:
    if (admin := get_channel_bot_admin()) is None:
        return _redirect(
            config.TRACECAT__PUBLIC_APP_URL,
            ok=False,
            message="Channel bots require Tracecat Enterprise",
        )
    try:
        # The browser's own session, so an install can be bound to whoever started it.
        result = await admin.complete_oauth(
            provider, callback, user_id=None if user is None else user.id
        )
    except Exception as exc:
        # The error can carry the OAuth code or provider response.
        logger.error(
            "Channel bot OAuth callback failed",
            provider=provider.value,
            error_type=type(exc).__name__,
        )
        return _redirect(
            config.TRACECAT__PUBLIC_APP_URL,
            ok=False,
            message="OAuth callback failed",
        )
    return _redirect(
        _same_app_or_default(result.return_url),
        ok=result.ok,
        message=result.message,
    )
