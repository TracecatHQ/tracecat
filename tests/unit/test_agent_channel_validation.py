from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tracecat.agent.channels.dependencies import _verify_slack_signature


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
