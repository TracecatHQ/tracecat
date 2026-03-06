"""Authenticated management API for external channel tokens."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tracecat.agent.channels.schemas import (
    AgentChannelTokenCreate,
    AgentChannelTokenRead,
    AgentChannelTokenUpdate,
    ChannelType,
    SlackChannelTokenConfig,
    SlackOAuthStartRequest,
    SlackOAuthStartResponse,
)
from tracecat.agent.channels.service import PENDING_SLACK_BOT_TOKEN, AgentChannelService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag


def _require_agent_channels_enabled() -> None:
    if not is_feature_enabled(FeatureFlag.AGENT_CHANNELS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feature not enabled",
        )


router = APIRouter(
    prefix="/agent/channels/tokens",
    tags=["agent-channels"],
    dependencies=[Depends(_require_agent_channels_enabled)],
)

WorkspaceEditorRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.post(
    "", response_model=AgentChannelTokenRead, status_code=status.HTTP_201_CREATED
)
@require_scope("agent:update")
async def create_channel_token(
    *,
    params: AgentChannelTokenCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentChannelTokenRead:
    service = AgentChannelService(session, role=role)
    try:
        token = await service.create_token(params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return service.to_read(token)


@router.get("", response_model=list[AgentChannelTokenRead])
@require_scope("agent:read")
async def list_channel_tokens(
    *,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
    agent_preset_id: uuid.UUID | None = Query(
        default=None, description="Filter by agent preset"
    ),
    channel_type: ChannelType | None = Query(
        default=None, description="Filter by channel type"
    ),
) -> list[AgentChannelTokenRead]:
    service = AgentChannelService(session, role=role)
    tokens = await service.list_tokens(
        agent_preset_id=agent_preset_id,
        channel_type=channel_type,
    )
    return [service.to_read(token) for token in tokens]


@router.patch("/{token_id}", response_model=AgentChannelTokenRead)
@require_scope("agent:update")
async def update_channel_token(
    *,
    token_id: uuid.UUID,
    params: AgentChannelTokenUpdate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentChannelTokenRead:
    service = AgentChannelService(session, role=role)
    token = await service.get_token(token_id)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel token '{token_id}' not found",
        )
    try:
        updated = await service.update_token(token, params)
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return service.to_read(updated)


@router.post("/{token_id}/rotate", response_model=AgentChannelTokenRead)
@require_scope("agent:update")
async def rotate_channel_token(
    *,
    token_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentChannelTokenRead:
    service = AgentChannelService(session, role=role)
    token = await service.get_token(token_id)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel token '{token_id}' not found",
        )
    rotated = await service.rotate_token_signature(token)
    return service.to_read(rotated)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def delete_channel_token(
    *,
    token_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> None:
    service = AgentChannelService(session, role=role)
    token = await service.get_token(token_id)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel token '{token_id}' not found",
        )
    await service.delete_token(token)


@router.post("/slack/oauth/start", response_model=SlackOAuthStartResponse)
@require_scope("agent:update")
async def start_slack_oauth(
    *,
    params: SlackOAuthStartRequest,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SlackOAuthStartResponse:
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_id is required",
        )
    service = AgentChannelService(session, role=role)

    token = None
    if params.token_id is not None:
        token = await service.get_token(params.token_id)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Channel token '{params.token_id}' not found",
            )
        if token.agent_preset_id != params.agent_preset_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Provided token does not match the requested agent preset "
                    f"('{params.agent_preset_id}')"
                ),
            )

    if token is None:
        try:
            token = await service.create_token(
                AgentChannelTokenCreate(
                    agent_preset_id=params.agent_preset_id,
                    channel_type=ChannelType.SLACK,
                    config=SlackChannelTokenConfig(
                        slack_bot_token=PENDING_SLACK_BOT_TOKEN,
                        slack_client_id=params.client_id,
                        slack_client_secret=params.client_secret,
                        slack_signing_secret=params.signing_secret,
                    ),
                    is_active=False,
                )
            )
        except TracecatNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except TracecatValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
    else:
        existing_config = service.parse_stored_channel_config(
            channel_type=ChannelType(token.channel_type),
            config_payload=token.config,
        )
        updated = await service.update_token(
            token,
            AgentChannelTokenUpdate(
                config=SlackChannelTokenConfig(
                    slack_bot_token=existing_config.slack_bot_token,
                    slack_client_id=params.client_id,
                    slack_client_secret=params.client_secret,
                    slack_signing_secret=params.signing_secret,
                ),
                is_active=False,
            ),
        )
        token = updated

    state = service.create_slack_oauth_state(
        token_id=token.id,
        workspace_id=role.workspace_id,
        return_url=params.return_url,
    )
    authorization_url = service.build_slack_oauth_authorization_url(
        client_id=params.client_id,
        state=state,
    )
    return SlackOAuthStartResponse(
        authorization_url=authorization_url,
        token=service.to_read(token),
    )
