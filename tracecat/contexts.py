from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

import loguru

from tracecat.dsl.models import RunContext
from tracecat.ee.interactions.models import InteractionContext
from tracecat.types.auth import Role

if TYPE_CHECKING:
    from tracecat.dsl.scheduler import StreamID

__all__ = [
    "ctx_run",
    "ctx_role",
    "ctx_logger",
    "ctx_interaction",
    "get_env",
    "ctx_stream_id",
]

ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)  # type: ignore
ctx_role: ContextVar[Role] = ContextVar("role", default=None)  # type: ignore
ctx_logger: ContextVar[loguru.Logger] = ContextVar("logger", default=None)  # type: ignore
ctx_interaction: ContextVar[InteractionContext | None] = ContextVar(
    "interaction", default=None
)
ctx_stream_id: ContextVar[StreamID | None] = ContextVar("stream-id", default=None)


ctx_env: ContextVar[dict[str, str] | None] = ContextVar("env", default=None)


def get_env() -> dict[str, str]:
    return ctx_env.get() or {}
