from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from tracecat import config
from tracecat.agent.tokens import (
    AGENT_OTEL_TOKEN_AUDIENCE,
    AGENT_OTEL_TOKEN_ISSUER,
    AGENT_OTEL_TOKEN_SUBJECT,
    mint_agent_otel_token,
    mint_mcp_token,
    verify_agent_otel_token,
    verify_mcp_token,
)
from tracecat.auth.secrets import get_service_key


def test_mcp_token_round_trips_parent_agent_workflow_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        allowed_actions=["core.http_request"],
        session_id=session_id,
        parent_agent_workflow_id=f"agent/{session_id}",
        parent_agent_run_id="run-123",
    )

    claims = verify_mcp_token(token)
    assert claims.workspace_id == workspace_id
    assert claims.organization_id == organization_id
    assert claims.session_id == session_id
    assert claims.user_id == user_id
    assert claims.parent_agent_workflow_id == f"agent/{session_id}"
    assert claims.parent_agent_run_id == "run-123"


def test_agent_otel_token_round_trips(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()

    token = mint_agent_otel_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        session_id=session_id,
    )

    claims = verify_agent_otel_token(token)
    assert claims.workspace_id == workspace_id
    assert claims.organization_id == organization_id
    assert claims.session_id == session_id


def test_agent_otel_token_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    with pytest.raises(ValueError, match="Invalid Agent OTel token"):
        verify_agent_otel_token("not-a-jwt")


def test_agent_otel_token_rejects_wrong_audience(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    payload = _agent_otel_payload(aud="wrong-audience")
    token = jwt.encode(payload, get_service_key(), algorithm="HS256")

    with pytest.raises(ValueError, match="Invalid Agent OTel token"):
        verify_agent_otel_token(token)


def test_agent_otel_token_rejects_wrong_subject(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    payload = _agent_otel_payload(sub="wrong-subject")
    token = jwt.encode(payload, get_service_key(), algorithm="HS256")

    with pytest.raises(ValueError, match="Invalid Agent OTel token subject"):
        verify_agent_otel_token(token)


def test_agent_otel_token_rejects_expired_token(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    token = mint_agent_otel_token(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        ttl_seconds=-1,
    )

    with pytest.raises(ValueError, match="Invalid Agent OTel token"):
        verify_agent_otel_token(token)


def _agent_otel_payload(
    *,
    aud: str = AGENT_OTEL_TOKEN_AUDIENCE,
    sub: str = AGENT_OTEL_TOKEN_SUBJECT,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "iss": AGENT_OTEL_TOKEN_ISSUER,
        "aud": aud,
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
    }
