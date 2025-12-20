from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tracecat import config
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.executor_tokens import (
    EXECUTOR_TOKEN_AUDIENCE,
    EXECUTOR_TOKEN_ISSUER,
    mint_executor_token,
    verify_executor_token,
)
from tracecat.auth.types import AccessLevel, Role
from tracecat.feature_flags import FeatureFlag


def _make_request(token: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    return Request(scope)


def _make_executor_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        access_level=AccessLevel.ADMIN,
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),
    )


def test_mint_and_verify_executor_token_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    role = _make_executor_role(uuid.uuid4())
    token = mint_executor_token(
        role=role,
        run_id="run-1",
        workflow_id="wf-1",
        ttl_seconds=60,
    )

    verified = verify_executor_token(token)

    assert verified.model_dump() == role.model_dump()


def test_verify_executor_token_expired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    role = _make_executor_role(uuid.uuid4())
    token = mint_executor_token(role=role, ttl_seconds=-1)

    with pytest.raises(ValueError):
        verify_executor_token(token)


def test_verify_executor_token_invalid_subject(monkeypatch: pytest.MonkeyPatch):
    """Verify that tokens with incorrect subject claim are rejected."""
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)

    role = _make_executor_role(uuid.uuid4())
    now = datetime.now(UTC)

    # Create a token with wrong subject
    payload = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": "wrong-subject",  # Invalid subject
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "role": role.model_dump(mode="json"),
    }
    token = jwt.encode(payload, service_key, algorithm="HS256")

    with pytest.raises(ValueError, match="Invalid executor token subject"):
        verify_executor_token(token)


@pytest.mark.anyio
async def test_role_dependency_executor_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.EXECUTOR_AUTH})

    workspace_id = uuid.uuid4()
    role = _make_executor_role(workspace_id)
    token = mint_executor_token(role=role, ttl_seconds=60)
    request = _make_request(token)

    resolved = await _role_dependency(
        request=request,
        session=AsyncMock(),
        workspace_id=workspace_id,
        user=None,
        api_key=None,
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    )

    assert resolved.model_dump() == role.model_dump()


@pytest.mark.anyio
async def test_role_dependency_executor_workspace_mismatch(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.EXECUTOR_AUTH})

    role = _make_executor_role(uuid.uuid4())
    token = mint_executor_token(role=role, ttl_seconds=60)
    request = _make_request(token)

    with pytest.raises(HTTPException) as exc:
        await _role_dependency(
            request=request,
            session=AsyncMock(),
            workspace_id=uuid.uuid4(),
            user=None,
            api_key=None,
            allow_user=False,
            allow_service=False,
            allow_executor=True,
            require_workspace="yes",
        )

    assert exc.value.status_code == 403
