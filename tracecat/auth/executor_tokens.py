from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, ValidationError

from tracecat import config
from tracecat.auth.secrets import get_service_key
from tracecat.identifiers import InternalServiceID, UserID, WorkspaceID

# Provenance of a ``core.script.run_python`` token. ``agent`` is untrusted,
# model/user-authored root Python bounded by the Agent toolset ceiling.
# ``registry_template`` marks a run-python step that lives inside an immutable,
# registry-locked template; that code is trusted by the lock, so the gateway
# exempts it from the toolset ceiling while caller-scope RBAC still applies.
# The claim is signed and stamped only at the trusted template-step boundary,
# so an agent-authored token can never forge it. Absent claim => ``agent``.
RunPythonOrigin = Literal["agent", "registry_template"]

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
    """Signed authorization context for sandbox-originated SDK calls.

    ``scopes`` is the caller Role bound. ``allowed_actions`` is the independent
    Agent toolset bound. ``action`` identifies the sandbox action that minted the
    token, so the gateway can restrict user-authored run-python code without
    changing ordinary workflow execution.
    """

    workspace_id: WorkspaceID
    user_id: UserID | None
    service_id: InternalServiceID | None = None
    scopes: frozenset[str] | None = None
    allowed_actions: frozenset[str] | None = None
    action: str | None = None
    run_python_origin: RunPythonOrigin = "agent"
    wf_id: str
    wf_exec_id: str


def mint_executor_token(
    *,
    workspace_id: WorkspaceID,
    user_id: UserID | None,
    service_id: InternalServiceID = "tracecat-executor",
    scopes: frozenset[str] | None = None,
    allowed_actions: frozenset[str] | None = None,
    action: str | None = None,
    run_python_origin: RunPythonOrigin = "agent",
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
    if scopes is not None:
        payload["scopes"] = sorted(scopes)
    if allowed_actions is not None:
        payload["allowed_actions"] = sorted(allowed_actions)
    if action is not None:
        payload["action"] = action
    # Only the non-default provenance is written; absent claim verifies as
    # ``agent``, keeping legacy tokens fail-closed toward enforcement.
    if run_python_origin != "agent":
        payload["run_python_origin"] = run_python_origin

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
        token_payload = ExecutorTokenPayload(
            workspace_id=payload["workspace_id"],
            user_id=payload.get("user_id"),
            service_id=payload.get("service_id"),
            scopes=payload.get("scopes"),
            allowed_actions=payload.get("allowed_actions"),
            action=payload.get("action"),
            run_python_origin=payload.get("run_python_origin", "agent"),
            wf_id=payload["wf_id"],
            wf_exec_id=payload["wf_exec_id"],
        )
    except (KeyError, ValidationError) as exc:
        raise ValueError("Executor token payload is invalid") from exc

    return token_payload
