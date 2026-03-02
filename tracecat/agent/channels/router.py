"""Public external channel endpoint router."""

from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channels.dependencies import validate_channel_token
from tracecat.agent.channels.handlers import build_channel_handler
from tracecat.agent.channels.schemas import ChannelType, ValidatedChannelToken
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Workspace
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
        body = await request.json()
    except ValueError:
        return Response(status_code=status.HTTP_200_OK)
    if not isinstance(body, dict):
        return Response(status_code=status.HTTP_200_OK)

    event_type = body.get("type")
    if event_type == "url_verification":
        challenge = body.get("challenge")
        if isinstance(challenge, str):
            return {"challenge": challenge}
        return Response(status_code=status.HTTP_200_OK)

    if event_type != "event_callback":
        return Response(status_code=status.HTTP_200_OK)

    background_tasks.add_task(
        _process_channel_event_async,
        channel_type=channel_type,
        validated_token=validated_token,
        payload=body,
    )
    return Response(status_code=status.HTTP_200_OK)
