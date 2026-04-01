from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from tracecat.auth.types import Role
from tracecat.context_state import (
    ctx_request_id,
    ctx_role,
    ctx_run,
    ctx_session_id,
)


def _coerce_role(value: Any) -> Role | None:
    if isinstance(value, Role):
        return value
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        payload = value
    else:
        return None

    try:
        return Role.model_validate(payload)
    except Exception:
        return None


def _inject_role_fields(record: dict[str, Any], role: Role | None) -> None:
    if role is None:
        return
    if role.organization_id is not None:
        record.setdefault("organization_id", str(role.organization_id))
    if role.workspace_id is not None:
        record.setdefault("workspace_id", str(role.workspace_id))
    if role.user_id is not None:
        record.setdefault("user_id", str(role.user_id))
    record.setdefault("role_type", role.type)
    record.setdefault("role_service_id", role.service_id)


def inject_context_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    injected = dict(record)

    if (role := _coerce_role(injected.pop("role", None))) is None:
        role = ctx_role.get()
    _inject_role_fields(injected, role)

    if (run_context := ctx_run.get()) is not None:
        injected.setdefault("wf_id", str(run_context.wf_id))
        injected.setdefault("wf_exec_id", str(run_context.wf_exec_id))
        injected.setdefault("wf_run_id", str(run_context.wf_run_id))

    if (request_id := ctx_request_id.get()) is not None:
        injected.setdefault("request_id", request_id)
    if (session_id := ctx_session_id.get()) is not None:
        injected.setdefault("session_id", str(session_id))
    return injected
