from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime

import loguru
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.dsl.schemas import ROOT_STREAM, RunContext, StreamID
from tracecat.interactions.schemas import InteractionContext

__all__ = [
    "ctx_run",
    "ctx_role",
    "ctx_logger",
    "ctx_interaction",
    "ctx_stream_id",
    "ctx_session",
    "ctx_client_ip",
    "ctx_logical_time",
    "get_env",
]

ctx_run: ContextVar[RunContext | None] = ContextVar("run", default=None)
ctx_role: ContextVar[Role | None] = ContextVar("role", default=None)
ctx_logger: ContextVar[loguru.Logger | None] = ContextVar("logger", default=None)
ctx_interaction: ContextVar[InteractionContext | None] = ContextVar(
    "interaction", default=None
)
ctx_client_ip: ContextVar[str | None] = ContextVar("client-ip", default=None)
ctx_stream_id: ContextVar[StreamID] = ContextVar("stream-id", default=ROOT_STREAM)
ctx_env: ContextVar[dict[str, str] | None] = ContextVar("env", default=None)
ctx_session: ContextVar[AsyncSession | None] = ContextVar("session", default=None)
ctx_session_id: ContextVar[uuid.UUID | None] = ContextVar("session-id", default=None)
"""ID for a streamable session, if any."""

ctx_logical_time: ContextVar[datetime | None] = ContextVar("logical-time", default=None)
"""Current logical time = time_anchor + elapsed workflow time. Used by FN.now()."""


@asynccontextmanager
async def with_session(session: AsyncSession):
    """Set the session in the context."""
    token = ctx_session.set(session)
    try:
        yield
    finally:
        ctx_session.reset(token)


def get_env() -> dict[str, str]:
    return ctx_env.get() or {}
