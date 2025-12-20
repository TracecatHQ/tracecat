from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError
from pydantic import ValidationError

from tracecat import config
from tracecat.auth.types import Role

EXECUTOR_TOKEN_ISSUER = "tracecat-executor"
EXECUTOR_TOKEN_AUDIENCE = "tracecat-api"
EXECUTOR_TOKEN_SUBJECT = "tracecat-executor"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "role")


def mint_executor_token(
    *,
    role: Role,
    run_id: str | None = None,
    workflow_id: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed executor JWT containing the full Role payload."""
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
        "role": role.model_dump(mode="json"),
    }
    if run_id:
        payload["run_id"] = run_id
    if workflow_id:
        payload["workflow_id"] = workflow_id

    return jwt.encode(payload, config.TRACECAT__SERVICE_KEY, algorithm="HS256")


def verify_executor_token(token: str) -> Role:
    """Verify executor JWT and return the embedded Role."""
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

    role_payload = payload.get("role")
    if role_payload is None:
        raise ValueError("Executor token missing role claim")

    try:
        role = Role.model_validate(role_payload)
    except ValidationError as exc:
        raise ValueError("Executor token role claim is invalid") from exc

    return role
