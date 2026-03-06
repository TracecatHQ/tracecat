"""Public external channel endpoint router."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import orjson
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from slack_sdk.errors import SlackApiError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.channels.dependencies import validate_channel_token
from tracecat.agent.channels.handlers import build_channel_handler
from tracecat.agent.channels.schemas import (
    AgentChannelTokenUpdate,
    ChannelType,
    SlackChannelTokenConfig,
    ValidatedChannelToken,
)
from tracecat.agent.channels.service import AgentChannelService
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Workspace
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.logger import logger


def _require_agent_channels_enabled() -> None:
    if not is_feature_enabled(FeatureFlag.AGENT_CHANNELS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feature not enabled",
        )


router = APIRouter(
    prefix="/agent/channels",
    tags=["public"],
    dependencies=[Depends(_require_agent_channels_enabled)],
)


def _build_callback_redirect_url(
    *, base_url: str, status: str, message: str | None = None
) -> str:
    parsed = urlparse(base_url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params["slack_connect"] = status
    if message:
        query_params["slack_message"] = message
    query = urlencode(query_params)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            query,
            parsed.fragment,
        )
    )


def _sanitize_return_url(return_url: str) -> str:
    default_url = config.TRACECAT__PUBLIC_APP_URL
    parsed_return = urlparse(return_url)
    parsed_default = urlparse(default_url)
    if (
        parsed_return.scheme not in {"http", "https"}
        or parsed_return.netloc != parsed_default.netloc
    ):
        return default_url
    return return_url


async def _resolve_service_role_for_workspace(
    session: AsyncSession,
    *,
    workspace_id,
) -> Role:
    result = await session.execute(
        select(Workspace.organization_id).where(Workspace.id == workspace_id)
    )
    organization_id = result.scalar_one_or_none()
    if organization_id is None:
        raise ValueError(f"Workspace '{workspace_id}' not found")
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )


async def _process_channel_event_async(
    *,
    channel_type: ChannelType,
    validated_token: ValidatedChannelToken,
    payload: dict[str, Any],
) -> None:
    try:
        async with get_async_session_context_manager() as session:
            role = await _resolve_service_role_for_workspace(
                session, workspace_id=validated_token.workspace_id
            )
            ctx_role.set(role)
            handler = build_channel_handler(
                channel_type=channel_type,
                session=session,
                role=role,
            )
            await handler.handle(payload=payload, token=validated_token)
    except Exception:
        logger.exception(
            "Failed processing external channel event",
            workspace_id=str(validated_token.workspace_id),
            preset_id=str(validated_token.agent_preset_id),
            channel_type=channel_type.value,
        )


@router.post("/{channel_type}/{token}", response_model=None)
async def handle_channel_event(
    *,
    channel_type: ChannelType,
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncDBSession,
) -> dict[str, str] | Response:
    """Receive public external channel events."""

    validated_token = await validate_channel_token(
        channel_type=channel_type,
        token=token,
        request=request,
        session=session,
    )
    try:
        raw_body = await request.body()
    except ValueError:
        return Response(status_code=status.HTTP_200_OK)

    content_type = request.headers.get("content-type", "").lower()
    body: dict[str, Any] | None = None
    if content_type.startswith("application/x-www-form-urlencoded"):
        try:
            decoded_body = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(status_code=status.HTTP_200_OK)
        parsed = parse_qs(decoded_body, keep_blank_values=True)
        payload_values = parsed.get("payload")
        if payload_values:
            try:
                parsed_payload = orjson.loads(payload_values[0])
            except orjson.JSONDecodeError:
                return Response(status_code=status.HTTP_200_OK)
            if isinstance(parsed_payload, dict):
                body = parsed_payload
    else:
        try:
            parsed_payload = orjson.loads(raw_body)
        except orjson.JSONDecodeError:
            return Response(status_code=status.HTTP_200_OK)
        if isinstance(parsed_payload, dict):
            body = parsed_payload

    if body is None:
        return Response(status_code=status.HTTP_200_OK)

    event_type = body.get("type")
    if event_type == "url_verification":
        challenge = body.get("challenge")
        if isinstance(challenge, str):
            return {"challenge": challenge}
        return Response(status_code=status.HTTP_200_OK)

    if event_type not in {"event_callback", "block_actions"}:
        return Response(status_code=status.HTTP_200_OK)

    background_tasks.add_task(
        _process_channel_event_async,
        channel_type=channel_type,
        validated_token=validated_token,
        payload=body,
    )
    return Response(status_code=status.HTTP_200_OK)


@router.get("/slack/oauth/callback")
async def handle_slack_oauth_callback(
    *,
    code: str | None = Query(default=None),
    state: str,
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    try:
        state_payload = AgentChannelService.parse_slack_oauth_state(state)
    except TracecatValidationError:
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=config.TRACECAT__PUBLIC_APP_URL,
                status="error",
                message="Invalid or expired OAuth state",
            )
        )

    return_url = _sanitize_return_url(state_payload["return_url"])
    if error is not None:
        message = error_description or error
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=return_url,
                status="error",
                message=message,
            )
        )
    if code is None:
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=return_url,
                status="error",
                message="Missing OAuth code",
            )
        )

    try:
        token_id = uuid.UUID(state_payload["token_id"])
        workspace_id = uuid.UUID(state_payload["workspace_id"])
    except ValueError:
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=return_url,
                status="error",
                message="Invalid OAuth state",
            )
        )

    try:
        async with get_async_session_context_manager() as session:
            role = await _resolve_service_role_for_workspace(
                session, workspace_id=workspace_id
            )
            service = AgentChannelService(session, role=role)
            token = await service.get_token(token_id)
            if token is None:
                raise TracecatNotFoundError(f"Channel token '{token_id}' not found")

            slack_config = service.parse_stored_channel_config(
                channel_type=ChannelType(token.channel_type),
                config_payload=token.config,
            )
            client_id = slack_config.slack_client_id
            client_secret = slack_config.slack_client_secret
            if not client_id or not client_secret:
                raise TracecatValidationError(
                    "Slack client ID and client secret are required"
                )

            bot_token = await service.exchange_slack_oauth_code(
                client_id=client_id,
                client_secret=client_secret,
                code=code,
            )
            await service.update_token(
                token,
                AgentChannelTokenUpdate(
                    config=SlackChannelTokenConfig(
                        slack_bot_token=bot_token,
                        slack_client_id=client_id,
                        slack_client_secret=client_secret,
                        slack_signing_secret=slack_config.slack_signing_secret,
                    ),
                    is_active=True,
                ),
            )
    except (TracecatNotFoundError, TracecatValidationError) as exc:
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=return_url,
                status="error",
                message=str(exc),
            )
        )
    except SlackApiError as exc:
        message = str(exc)
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=return_url,
                status="error",
                message=message,
            )
        )
    except Exception as exc:
        logger.error("Slack OAuth callback failed", error=str(exc), exc_info=False)
        return RedirectResponse(
            _build_callback_redirect_url(
                base_url=return_url,
                status="error",
                message="Slack OAuth callback failed",
            )
        )

    return RedirectResponse(
        _build_callback_redirect_url(
            base_url=return_url,
            status="success",
        )
    )
