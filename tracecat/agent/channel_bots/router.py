"""Channel bot admin routes; without the enterprise provider they answer "requires Enterprise".

`/agent/channels/...` keeps the legacy token API until ENG-2380.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from tracecat import config
from tracecat.agent.channel_bots.provider import ChannelBotAdmin, get_channel_bot_admin
from tracecat.agent.channel_bots.schemas import (
    CHANNEL_BOTS_PREFIX,
    SLACK_OAUTH_CALLBACK_PATH,
    AgentChannelBotCreate,
    AgentChannelBotRead,
    AgentChannelBotUpdate,
    ChannelBotProvider,
    SlackBotOAuthCallback,
    SlackBotOAuthStartRequest,
    SlackBotOAuthStartResponse,
)
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, PageParams
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement


def _require_provider() -> ChannelBotAdmin:
    if (provider := get_channel_bot_admin()) is None:
        raise EntitlementRequired(
            Entitlement.AGENT_CHANNELS.value,
            message="Channel bots require Tracecat Enterprise",
        )
    return provider


async def _require_agent_channels_entitlement(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> None:
    await check_entitlement(session, role, Entitlement.AGENT_CHANNELS)


Admin = Annotated[ChannelBotAdmin, Depends(_require_provider)]

router = APIRouter(
    prefix=CHANNEL_BOTS_PREFIX,
    tags=["agent-channel-bots"],
    dependencies=[Depends(_require_agent_channels_entitlement)],
)
public_router = APIRouter(tags=["public"])


async def _call[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except TracecatNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
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
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None, max_length=8192),
) -> CursorPaginatedResponse[AgentChannelBotRead]:
    return await _call(
        admin.list_bots(
            session,
            role,
            agent_preset_id=agent_preset_id,
            provider=provider,
            page=PageParams(limit=limit, cursor=cursor),
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


@router.post("/slack/oauth/start", response_model=SlackBotOAuthStartResponse)
@require_scope("agent:update")
async def start_slack_bot_oauth(
    *,
    params: SlackBotOAuthStartRequest,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    admin: Admin,
) -> SlackBotOAuthStartResponse:
    return await _call(admin.start_slack_oauth(session, role, params))


def _redirect(base_url: str, *, ok: bool, message: str | None) -> RedirectResponse:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["slack_connect"] = "success" if ok else "error"
    if message:
        query["slack_message"] = message
    return RedirectResponse(urlunparse(parsed._replace(query=urlencode(query))))


def _same_app_or_default(return_url: str | None) -> str:
    default_url = config.TRACECAT__PUBLIC_APP_URL
    if return_url is None:
        return default_url
    parsed = urlparse(return_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc != urlparse(default_url).netloc
    ):
        return default_url
    return return_url


@public_router.get(SLACK_OAUTH_CALLBACK_PATH, include_in_schema=False)
async def handle_slack_bot_oauth_callback(
    *,
    state: str,
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    callback = SlackBotOAuthCallback(
        state=state, code=code, error=error, error_description=error_description
    )
    if (admin := get_channel_bot_admin()) is None:
        return _redirect(
            config.TRACECAT__PUBLIC_APP_URL,
            ok=False,
            message="Channel bots require Tracecat Enterprise",
        )
    try:
        outcome = await admin.complete_slack_oauth(callback)
    except Exception as exc:
        logger.error("Slack bot OAuth callback failed", error=repr(exc))
        return _redirect(
            config.TRACECAT__PUBLIC_APP_URL,
            ok=False,
            message="Slack OAuth callback failed",
        )
    return _redirect(
        _same_app_or_default(outcome.return_url),
        ok=outcome.ok,
        message=outcome.message,
    )
