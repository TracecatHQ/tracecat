from __future__ import annotations

from contextvars import ContextVar

import loguru

from tracecat.dsl.models import RunContext
from tracecat.ee.interactions.models import InteractionContext
from tracecat.types.auth import Role

ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)  # type: ignore
ctx_role: ContextVar[Role] = ContextVar("role", default=None)  # type: ignore
ctx_logger: ContextVar[loguru.Logger] = ContextVar("logger", default=None)  # type: ignore
ctx_interaction: ContextVar[InteractionContext | None] = ContextVar(
    "interaction", default=None
)


ctx_env: ContextVar[dict[str, str] | None] = ContextVar("env", default=None)


def get_env() -> dict[str, str]:
    return ctx_env.get() or {}
