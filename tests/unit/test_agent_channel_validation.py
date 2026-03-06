from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tracecat.agent.channels.dependencies import (
    _verify_slack_signature,
    validate_channel_token,
)
from tracecat.agent.channels.schemas import ChannelType
from tracecat.agent.channels.service import PENDING_SLACK_BOT_TOKEN, AgentChannelService


def _build_request(headers: dict[str, str], body: bytes) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope, receive)


def _build_slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    signature_base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), signature_base, hashlib.sha256).hexdigest()


def test_verify_slack_signature_accepts_valid_request() -> None:
    secret = "signing-secret"
    body = b'{"type":"event_callback"}'
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    signature = _build_slack_signature(secret, timestamp, body)
    request = _build_request(
        {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
        body,
    )

    _verify_slack_signature(
        request=request,
        body=body,
        slack_signing_secret=secret,
    )


def test_verify_slack_signature_rejects_stale_timestamp() -> None:
    secret = "signing-secret"
    body = b'{"type":"event_callback"}'
    stale_timestamp = str(
        int((datetime.now(tz=UTC) - timedelta(minutes=10)).timestamp())
    )
    signature = _build_slack_signature(secret, stale_timestamp, body)
    request = _build_request(
        {
            "X-Slack-Request-Timestamp": stale_timestamp,
            "X-Slack-Signature": signature,
        },
        body,
    )

    with pytest.raises(HTTPException) as exc_info:
        _verify_slack_signature(
            request=request,
            body=body,
            slack_signing_secret=secret,
        )
    assert exc_info.value.status_code == 401


def test_verify_slack_signature_rejects_invalid_signature() -> None:
    secret = "signing-secret"
    body = b'{"type":"event_callback"}'
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    request = _build_request(
        {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": "v0=invalid",
        },
        body,
    )

    with pytest.raises(HTTPException) as exc_info:
        _verify_slack_signature(
            request=request,
            body=body,
            slack_signing_secret=secret,
        )
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_validate_channel_token_allows_url_verification_for_pending_inactive_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_id = uuid.uuid4()
    body = b'{"type":"url_verification","challenge":"abc"}'
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    signature = _build_slack_signature("signing-secret", timestamp, body)
    request = _build_request(
        {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
        body,
    )
    token_record = AsyncMock()
    token_record.id = token_id
    token_record.workspace_id = uuid.uuid4()
    token_record.agent_preset_id = uuid.uuid4()
    token_record.is_active = False
    token_record.config = {
        "slack_bot_token": PENDING_SLACK_BOT_TOKEN,
        "slack_signing_secret": "signing-secret",
    }
    get_token_mock = AsyncMock(return_value=token_record)

    monkeypatch.setattr(
        AgentChannelService,
        "parse_public_token",
        lambda _: (token_id, "sig"),
    )
    monkeypatch.setattr(
        AgentChannelService,
        "verify_public_token_signature",
        lambda _token_id, _sig: True,
    )
    monkeypatch.setattr(
        AgentChannelService,
        "get_token_for_public_request",
        get_token_mock,
    )

    validated = await validate_channel_token(
        channel_type=ChannelType.SLACK,
        token="public-token",
        request=request,
        session=AsyncMock(),
    )

    assert validated.id == token_id
    assert validated.config.slack_bot_token == PENDING_SLACK_BOT_TOKEN
    await_args = get_token_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["require_active"] is False


@pytest.mark.anyio
async def test_validate_channel_token_rejects_inactive_token_for_event_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_id = uuid.uuid4()
    body = b'{"type":"event_callback","event":{"type":"app_mention"}}'
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    signature = _build_slack_signature("signing-secret", timestamp, body)
    request = _build_request(
        {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
        body,
    )
    token_record = AsyncMock()
    token_record.id = token_id
    token_record.workspace_id = uuid.uuid4()
    token_record.agent_preset_id = uuid.uuid4()
    token_record.is_active = False
    token_record.config = {
        "slack_bot_token": "xoxb-ready",
        "slack_signing_secret": "signing-secret",
    }
    get_token_mock = AsyncMock(return_value=token_record)

    monkeypatch.setattr(
        AgentChannelService,
        "parse_public_token",
        lambda _: (token_id, "sig"),
    )
    monkeypatch.setattr(
        AgentChannelService,
        "verify_public_token_signature",
        lambda _token_id, _sig: True,
    )
    monkeypatch.setattr(
        AgentChannelService,
        "get_token_for_public_request",
        get_token_mock,
    )

    with pytest.raises(HTTPException) as exc_info:
        await validate_channel_token(
            channel_type=ChannelType.SLACK,
            token="public-token",
            request=request,
            session=AsyncMock(),
        )

    assert exc_info.value.status_code == 401
    await_args = get_token_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["require_active"] is True
