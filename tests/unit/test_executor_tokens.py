from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from starlette.requests import Request

from tracecat import config
from tracecat.auth import credentials
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.executor_tokens import (
    EXECUTOR_TOKEN_AUDIENCE,
    EXECUTOR_TOKEN_ISSUER,
    mint_executor_token,
    verify_executor_token,
)
from tracecat.auth.types import AccessLevel


def _make_request(token: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    return Request(scope)


def test_mint_and_verify_executor_token_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    wf_id = "wf-1"
    wf_exec_id = "run-1"

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=user_id,
        wf_id=wf_id,
        wf_exec_id=wf_exec_id,
        ttl_seconds=60,
    )

    verified = verify_executor_token(token)

    assert verified.workspace_id == workspace_id
    assert verified.user_id == user_id
    assert verified.wf_id == wf_id
    assert verified.wf_exec_id == wf_exec_id


def test_verify_executor_token_expired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=-1,
    )

    with pytest.raises(ValueError):
        verify_executor_token(token)


def test_verify_executor_token_invalid_subject(monkeypatch: pytest.MonkeyPatch):
    """Verify that tokens with incorrect subject claim are rejected."""
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)

    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)

    # Create a token with wrong subject
    payload = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": "wrong-subject",  # Invalid subject
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(workspace_id),
        "user_id": str(user_id),
        "wf_id": "wf-1",
        "wf_exec_id": "run-1",
    }
    token = jwt.encode(payload, service_key, algorithm="HS256")

    with pytest.raises(ValueError, match="Invalid executor token subject"):
        verify_executor_token(token)


def test_verify_executor_token_with_null_user_id(monkeypatch: pytest.MonkeyPatch):
    """Verify that tokens with null user_id are valid (system/anonymous executions)."""
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=None,
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )

    verified = verify_executor_token(token)

    assert verified.workspace_id == workspace_id
    assert verified.user_id is None
    assert verified.wf_id == "wf-1"
    assert verified.wf_exec_id == "run-1"


def _mock_session_with_user(user_role):
    """Create a mock session that returns the given user role.

    The executor authentication queries User.role to determine access level.
    The workspace->org lookup is now cached via _get_workspace_org_id.
    """
    user_role_result = MagicMock()
    user_role_result.scalar_one_or_none.return_value = user_role

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=user_role_result)
    return mock_session


@pytest.mark.anyio
async def test_role_dependency_executor_token_derives_access_level_from_db(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that access_level is derived from DB lookup, not from token."""
    from tracecat.auth.schemas import UserRole

    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    # Mock the cached workspace->org lookup
    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=user_id,
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )
    request = _make_request(token)

    # Mock session to return ADMIN role from DB
    mock_session = _mock_session_with_user(UserRole.ADMIN)

    resolved = await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=workspace_id,
        user=None,
        api_key=None,
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    )

    # Verify access_level was derived from DB (ADMIN)
    assert resolved.type == "service"
    assert resolved.service_id == "tracecat-executor"
    assert resolved.workspace_id == workspace_id
    assert resolved.user_id == user_id
    assert resolved.access_level == AccessLevel.ADMIN
    assert resolved.organization_id == organization_id


@pytest.mark.anyio
async def test_role_dependency_executor_token_defaults_to_basic_for_unknown_user(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that access_level defaults to BASIC when user is not found in DB."""
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    # Mock the cached workspace->org lookup
    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=user_id,
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )
    request = _make_request(token)

    # Mock session to return None (user not found)
    mock_session = _mock_session_with_user(None)

    resolved = await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=workspace_id,
        user=None,
        api_key=None,
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    )

    # Should default to BASIC access level
    assert resolved.access_level == AccessLevel.BASIC


@pytest.mark.anyio
async def test_role_dependency_executor_token_defaults_to_basic_for_null_user(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that access_level defaults to BASIC when user_id is null (system execution)."""
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    # Mock the cached workspace->org lookup
    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=None,  # System/anonymous execution
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )
    request = _make_request(token)

    # No user role lookup needed since user_id is None
    mock_session = AsyncMock()

    resolved = await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=workspace_id,
        user=None,
        api_key=None,
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    )

    # Should default to BASIC access level
    assert resolved.access_level == AccessLevel.BASIC
    assert resolved.user_id is None
    assert resolved.organization_id == organization_id


@pytest.mark.anyio
async def test_role_dependency_executor_uses_jwt_workspace(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that workspace_id is extracted from JWT, not query param.

    The workspace_id query param is no longer validated - the Role's
    workspace_id comes entirely from the JWT token.
    """
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    jwt_workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    # Mock the cached workspace->org lookup
    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == jwt_workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)

    token = mint_executor_token(
        workspace_id=jwt_workspace_id,
        user_id=uuid.uuid4(),
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )
    request = _make_request(token)

    # Mock session to return user role (user not found)
    mock_session = _mock_session_with_user(None)

    # Pass None for workspace_id - it should come from JWT
    resolved = await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=None,
        user=None,
        api_key=None,
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    )

    # The resolved role should have workspace_id from the JWT
    assert resolved.workspace_id == jwt_workspace_id
    assert resolved.type == "service"
    assert resolved.service_id == "tracecat-executor"
    assert resolved.organization_id == organization_id
