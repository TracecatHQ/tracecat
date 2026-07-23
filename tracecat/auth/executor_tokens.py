from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, ValidationError

from tracecat import config
from tracecat.auth.secrets import get_service_key
from tracecat.identifiers import InternalServiceID, UserID, WorkspaceID

ExecutionOrigin = Literal["agent"]
"""Attested provenance for code authored by an Agent."""

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
    user_id: UserID | None = None
    service_id: InternalServiceID | None = None
    execution_origin: ExecutionOrigin | None = None
    wf_id: str
    wf_exec_id: str


def mint_executor_token(
    *,
    workspace_id: WorkspaceID,
    user_id: UserID | None,
    service_id: InternalServiceID = "tracecat-executor",
    execution_origin: ExecutionOrigin | None = None,
    wf_id: str,
    wf_exec_id: str,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed executor JWT scoped to a specific workflow execution."""
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
    if execution_origin is not None:
        payload["execution_origin"] = execution_origin

    return jwt.encode(payload, get_service_key(), algorithm="HS256")


def verify_executor_token(token: str) -> ExecutorTokenPayload:
    """Verify executor JWT and return the token payload.

    Returns the ExecutorTokenPayload containing workspace_id, user_id, service_id,
    wf_id, and wf_exec_id.
    """
    try:
        payload = jwt.decode(
            token,
            get_service_key(),
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
        return ExecutorTokenPayload.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("Executor token payload is invalid") from exc
