from __future__ import annotations

from contextvars import ContextVar

import loguru

from tracecat.dsl.models import ROOT_STREAM, RunContext, StreamID
from tracecat.ee.interactions.models import InteractionContext
from tracecat.types.auth import Role

__all__ = [
    "ctx_run",
    "ctx_role",
    "ctx_logger",
    "ctx_interaction",
    "ctx_stream_id",
    "get_env",
]

ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)  # type: ignore
ctx_role: ContextVar[Role] = ContextVar("role", default=None)  # type: ignore
ctx_logger: ContextVar[loguru.Logger] = ContextVar("logger", default=None)  # type: ignore
ctx_interaction: ContextVar[InteractionContext | None] = ContextVar(
    "interaction", default=None
)
ctx_stream_id: ContextVar[StreamID] = ContextVar("stream-id", default=ROOT_STREAM)
ctx_env: ContextVar[dict[str, str] | None] = ContextVar("env", default=None)


def get_env() -> dict[str, str]:
    return ctx_env.get() or {}
