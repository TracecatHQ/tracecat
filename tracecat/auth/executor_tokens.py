from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, ValidationError

from tracecat import config
from tracecat.identifiers import InternalServiceID, UserID, WorkspaceID

EXECUTOR_TOKEN_ISSUER = "tracecat-executor"
EXECUTOR_TOKEN_AUDIENCE = "tracecat-api"
EXECUTOR_TOKEN_SUBJECT = "tracecat-executor"
REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "iat",
    "exp",
    "workspace_id",
    "wf_id",
    "wf_exec_id",
)


class ExecutorTokenPayload(BaseModel):
    """Payload extracted from a verified executor JWT."""

    workspace_id: WorkspaceID
    user_id: UserID | None
    service_id: InternalServiceID | None = None
    wf_id: str
    wf_exec_id: str


def mint_executor_token(
    *,
    workspace_id: WorkspaceID,
    user_id: UserID | None,
    service_id: InternalServiceID = "tracecat-executor",
    wf_id: str,
    wf_exec_id: str,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed executor JWT scoped to a specific workflow execution."""
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    now = datetime.now(UTC)
    ttl = ttl_seconds or config.TRACECAT__EXECUTOR_TOKEN_TTL_SECONDS
    payload: dict[str, Any] = {
        "iss": EXECUTOR_TOKEN_ISSUER,
        "aud": EXECUTOR_TOKEN_AUDIENCE,
        "sub": EXECUTOR_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "workspace_id": str(workspace_id),
        "user_id": str(user_id) if user_id else None,
        "service_id": service_id,
        "wf_id": wf_id,
        "wf_exec_id": wf_exec_id,
    }

    return jwt.encode(payload, config.TRACECAT__SERVICE_KEY, algorithm="HS256")


def verify_executor_token(token: str) -> ExecutorTokenPayload:
    """Verify executor JWT and return the token payload.

    Returns the ExecutorTokenPayload containing workspace_id, user_id, service_id,
    wf_id, and wf_exec_id.
    """
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    try:
        payload = jwt.decode(
            token,
            config.TRACECAT__SERVICE_KEY,
            algorithms=["HS256"],
            audience=EXECUTOR_TOKEN_AUDIENCE,
            issuer=EXECUTOR_TOKEN_ISSUER,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except PyJWTError as exc:
        raise ValueError("Invalid executor token") from exc

    # PyJWT doesn't have a built-in subject parameter for validation,
    # so we must manually verify the sub claim value
    if payload.get("sub") != EXECUTOR_TOKEN_SUBJECT:
        raise ValueError("Invalid executor token subject")

    try:
        token_payload = ExecutorTokenPayload(
            workspace_id=payload["workspace_id"],
            user_id=payload.get("user_id"),
            service_id=payload.get("service_id"),
            wf_id=payload["wf_id"],
            wf_exec_id=payload["wf_exec_id"],
        )
    except (KeyError, ValidationError) as exc:
        raise ValueError("Executor token payload is invalid") from exc

    return token_payload
