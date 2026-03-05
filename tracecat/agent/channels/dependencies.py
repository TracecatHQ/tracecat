"""Dependencies for external channel request validation."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import orjson
from fastapi import HTTPException, Request, status
from pydantic import ValidationError

from tracecat.agent.channels.schemas import (
    ChannelType,
    ValidatedChannelToken,
)
from tracecat.agent.channels.service import PENDING_SLACK_BOT_TOKEN, AgentChannelService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError
from tracecat.logger import logger

SLACK_REQUEST_MAX_AGE_SECONDS = 300


def _reject_unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized channel request",
    )


def _verify_slack_signature(
    *,
    request: Request,
    body: bytes,
    slack_signing_secret: str,
) -> None:
    slack_signature = request.headers.get("X-Slack-Signature")
    slack_timestamp = request.headers.get("X-Slack-Request-Timestamp")
    if not slack_signature or not slack_timestamp:
        raise _reject_unauthorized()

    try:
        timestamp_int = int(slack_timestamp)
    except ValueError as exc:
        raise _reject_unauthorized() from exc

    current_timestamp = int(datetime.now(tz=UTC).timestamp())
    if abs(current_timestamp - timestamp_int) > SLACK_REQUEST_MAX_AGE_SECONDS:
        raise _reject_unauthorized()

    signature_base = b"v0:" + slack_timestamp.encode() + b":" + body
    expected_signature = (
        "v0="
        + hmac.new(
            slack_signing_secret.encode(),
            signature_base,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(slack_signature, expected_signature):
        raise _reject_unauthorized()


def _is_slack_url_verification(body: bytes) -> bool:
    try:
        payload = orjson.loads(body)
    except orjson.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("type") == "url_verification"


async def validate_channel_token(
    *,
    channel_type: ChannelType,
    token: str,
    request: Request,
    session: AsyncDBSession,
) -> ValidatedChannelToken:
    """Validate public channel token and channel-specific request signature."""

    try:
        token_id, sig_hex = AgentChannelService.parse_public_token(token)
    except TracecatValidationError as exc:
        logger.warning("Failed to parse channel token", error=str(exc))
        raise _reject_unauthorized() from exc

    if not AgentChannelService.verify_public_token_signature(token_id, sig_hex):
        raise _reject_unauthorized()

    body = await request.body()
    if channel_type is ChannelType.SLACK:
        is_url_verification = _is_slack_url_verification(body)
        token_record = await AgentChannelService.get_token_for_public_request(
            session,
            token_id=token_id,
            channel_type=channel_type,
            require_active=not is_url_verification,
        )
        if token_record is None:
            raise _reject_unauthorized()

        try:
            slack_config = AgentChannelService.parse_stored_channel_config(
                channel_type=channel_type,
                config_payload=token_record.config,
            )
        except (ValidationError, TracecatValidationError) as exc:
            logger.warning(
                "Invalid channel token config in database",
                channel_type=channel_type.value,
                token_id=str(token_record.id),
                error=str(exc),
            )
            raise _reject_unauthorized() from exc

        _verify_slack_signature(
            request=request,
            body=body,
            slack_signing_secret=slack_config.slack_signing_secret,
        )
        if not is_url_verification:
            if not token_record.is_active:
                raise _reject_unauthorized()
            if slack_config.slack_bot_token == PENDING_SLACK_BOT_TOKEN:
                raise _reject_unauthorized()
    else:
        raise _reject_unauthorized()

    return ValidatedChannelToken(
        id=token_record.id,
        workspace_id=token_record.workspace_id,
        agent_preset_id=token_record.agent_preset_id,
        channel_type=channel_type,
        config=slack_config,
        public_token=token,
    )
