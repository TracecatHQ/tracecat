from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from starlette.requests import Request

from tracecat import config
from tracecat.auth import credentials
from tracecat.auth.executor_tokens import (
    EXECUTOR_TOKEN_AUDIENCE,
    EXECUTOR_TOKEN_ISSUER,
    EXECUTOR_TOKEN_SUBJECT,
    mint_executor_token,
    verify_executor_token,
)


def _make_request(token: str) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())]
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    return Request(scope)


def _mint_legacy_executor_token_without_service_id(
    *,
    service_key: str,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    wf_id: str = "wf-1",
    wf_exec_id: str = "run-1",
    ttl_seconds: int = 60,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": EXECUTOR_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "workspace_id": str(workspace_id),
        "user_id": str(user_id) if user_id else None,
        "wf_id": wf_id,
        "wf_exec_id": wf_exec_id,
    }
    return jwt.encode(payload, service_key, algorithm="HS256")


def test_mint_and_verify_executor_token_roundtrip_service_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    token = mint_executor_token(
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-schedule-runner",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )

    verified = verify_executor_token(token)

    assert verified.service_id == "tracecat-schedule-runner"


def test_verify_executor_token_without_service_id_claim_is_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)

    token = _mint_legacy_executor_token_without_service_id(
        service_key=service_key,
        workspace_id=uuid.uuid4(),
        user_id=None,
    )

    verified = verify_executor_token(token)

    assert verified.service_id is None


@pytest.mark.anyio
async def test_authenticate_executor_defaults_service_id_when_claim_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_key = "test-service-key"
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", service_key)

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)

    token = _mint_legacy_executor_token_without_service_id(
        service_key=service_key,
        workspace_id=workspace_id,
        user_id=None,
    )

    role = await credentials._authenticate_executor(
        request=_make_request(token),
        workspace_id=workspace_id,
        require_workspace="yes",
    )

    assert role.service_id == "tracecat-executor"
    assert role.organization_id == organization_id


@pytest.mark.anyio
async def test_authenticate_executor_uses_service_id_claim_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    async def mock_get_workspace_org_id(ws_id: uuid.UUID) -> uuid.UUID | None:
        return organization_id if ws_id == workspace_id else None

    monkeypatch.setattr(credentials, "_get_workspace_org_id", mock_get_workspace_org_id)

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

    assert role.service_id == "tracecat-schedule-runner"
