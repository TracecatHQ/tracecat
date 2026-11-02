from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from starlette.requests import Request

from tracecat import config
from tracecat.auth import credentials
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.executor_tokens import (
    EXECUTOR_TOKEN_AUDIENCE,
    EXECUTOR_TOKEN_ISSUER,
    EXECUTOR_TOKEN_SUBJECT,
    ExecutionOrigin,
    mint_executor_token,
    verify_executor_token,
)
from tracecat.contexts import ctx_agent_session_id
from tracecat.identifiers import InternalServiceID


def _make_request(token: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    return Request(scope)


def _patch_workspace_org(
    monkeypatch: pytest.MonkeyPatch, workspace_id: uuid.UUID
) -> uuid.UUID:
    """Mock the cached workspace->org lookup and return the organization ID."""
    organization_id = uuid.uuid4()

    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)
    return organization_id


def _mint_legacy_executor_token_without_service_id(
    *, service_key: str, workspace_id: uuid.UUID
) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": EXECUTOR_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(workspace_id),
        "user_id": None,
        "wf_id": "wf-1",
        "wf_exec_id": "run-1",
    }
    return jwt.encode(payload, service_key, algorithm="HS256")


_AGENT_SESSION_ID = uuid.UUID(int=7)


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        pytest.param(
            {},
            {"execution_origin": None, "root_action": None, "agent_session_id": None},
            id="defaults",
        ),
        *[
            pytest.param(
                {"execution_origin": origin, "root_action": "core.script.run_python"},
                {"execution_origin": origin, "root_action": "core.script.run_python"},
                id=f"{origin}-origin",
            )
            for origin in ("agent", "workflow")
        ],
        pytest.param(
            {
                "service_id": "tracecat-mcp",
                "execution_origin": "agent",
                "agent_session_id": _AGENT_SESSION_ID,
            },
            {"agent_session_id": _AGENT_SESSION_ID},
            id="agent-session-id",
        ),
        pytest.param(
            {"service_id": "tracecat-schedule-runner"},
            {"service_id": "tracecat-schedule-runner"},
            id="service-id",
        ),
    ],
)
def test_mint_and_verify_executor_token_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=user_id,
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
        **claims,
    )

    verified = verify_executor_token(token)

    assert verified.workspace_id == workspace_id
    assert verified.user_id == user_id
    assert verified.wf_id == "wf-1"
    assert verified.wf_exec_id == "run-1"
    for attr, value in expected.items():
        assert getattr(verified, attr) == value


def test_verify_executor_token_without_service_id_claim_is_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)

    token = _mint_legacy_executor_token_without_service_id(
        service_key=service_key, workspace_id=uuid.uuid4()
    )

    verified = verify_executor_token(token)

    assert verified.service_id is None


@pytest.mark.parametrize(
    ("service_id", "execution_origin"),
    [
        pytest.param("tracecat-mcp", "workflow", id="workflow-origin"),
        pytest.param("tracecat-executor", "agent", id="non-mcp-service"),
    ],
)
def test_executor_token_rejects_untrusted_agent_session_origin(
    monkeypatch: pytest.MonkeyPatch,
    service_id: InternalServiceID,
    execution_origin: ExecutionOrigin,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    with pytest.raises(
        ValueError,
        match="agent_session_id requires the tracecat-mcp agent execution origin",
    ):
        mint_executor_token(
            workspace_id=uuid.uuid4(),
            user_id=None,
            service_id=service_id,
            execution_origin=execution_origin,
            agent_session_id=uuid.uuid4(),
            wf_id="wf-1",
            wf_exec_id="run-1",
        )


def test_verify_executor_token_rejects_inconsistent_agent_session_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)
    now = datetime.now(UTC)
    payload = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": "tracecat-executor",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(uuid.uuid4()),
        "service_id": "tracecat-executor",
        "execution_origin": "agent",
        "agent_session_id": str(uuid.uuid4()),
        "wf_id": "wf-1",
        "wf_exec_id": "run-1",
    }
    token = jwt.encode(payload, service_key, algorithm="HS256")

    with pytest.raises(ValueError, match="Executor token payload is invalid"):
        verify_executor_token(token)


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


@pytest.mark.anyio
async def test_role_dependency_executor_token_populates_org_context(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    organization_id = _patch_workspace_org(monkeypatch, workspace_id)
    agent_session_id = uuid.uuid4()

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=user_id,
        service_id="tracecat-mcp",
        execution_origin="agent",
        agent_session_id=agent_session_id,
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )
    request = _make_request(token)

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

    assert resolved.type == "service"
    assert resolved.service_id == "tracecat-mcp"
    assert resolved.workspace_id == workspace_id
    assert resolved.user_id == user_id
    assert resolved.organization_id == organization_id
    assert ctx_agent_session_id.get() == agent_session_id


@pytest.mark.anyio
async def test_role_dependency_workflow_token_clears_agent_session_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    workspace_id = uuid.uuid4()
    _patch_workspace_org(monkeypatch, workspace_id)
    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=None,
        wf_id="wf-1",
        wf_exec_id="run-1",
    )
    context_token = ctx_agent_session_id.set(uuid.uuid4())
    try:
        await _role_dependency(
            request=_make_request(token),
            session=AsyncMock(),
            workspace_id=workspace_id,
            user=None,
            api_key=None,
            allow_user=False,
            allow_service=False,
            allow_executor=True,
            require_workspace="yes",
        )

        assert ctx_agent_session_id.get() is None
    finally:
        ctx_agent_session_id.reset(context_token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "user_id",
    [pytest.param(uuid.uuid4(), id="unknown_user"), pytest.param(None, id="null_user")],
)
async def test_role_dependency_executor_token_defaults_to_basic_user(
    monkeypatch: pytest.MonkeyPatch,
    user_id: uuid.UUID | None,
) -> None:
    """Executor roles resolve without DB user context, including system runs."""
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = _patch_workspace_org(monkeypatch, workspace_id)

    token = mint_executor_token(
        workspace_id=workspace_id,
        user_id=user_id,
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )

    resolved = await _role_dependency(
        request=_make_request(token),
        session=AsyncMock(),
        workspace_id=workspace_id,
        user=None,
        api_key=None,
        allow_user=False,
        allow_service=False,
        allow_executor=True,
        require_workspace="yes",
    )

    assert resolved.type == "service"
    assert resolved.user_id == user_id
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
    organization_id = _patch_workspace_org(monkeypatch, jwt_workspace_id)

    token = mint_executor_token(
        workspace_id=jwt_workspace_id,
        user_id=uuid.uuid4(),
        wf_id="wf-1",
        wf_exec_id="run-1",
        ttl_seconds=60,
    )
    request = _make_request(token)

    mock_session = AsyncMock()

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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("legacy_token", "expected_service_id"),
    [
        pytest.param(True, "tracecat-executor", id="claim_missing_defaults"),
        pytest.param(False, "tracecat-schedule-runner", id="claim_present"),
    ],
)
async def test_authenticate_executor_service_id(
    monkeypatch: pytest.MonkeyPatch,
    legacy_token: bool,
    expected_service_id: str,
) -> None:
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)
    workspace_id = uuid.uuid4()
    organization_id = _patch_workspace_org(monkeypatch, workspace_id)

    if legacy_token:
        token = _mint_legacy_executor_token_without_service_id(
            service_key=service_key, workspace_id=workspace_id
        )
    else:
        token = mint_executor_token(
            workspace_id=workspace_id,
            user_id=None,
            service_id="tracecat-schedule-runner",
            wf_id="wf-1",
            wf_exec_id="run-1",
        )

    role = await credentials._authenticate_executor(
        request=_make_request(token),
        workspace_id=workspace_id,
        require_workspace="yes",
    )

    assert role.service_id == expected_service_id
    assert role.organization_id == organization_id
