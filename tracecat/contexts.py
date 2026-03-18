from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime

import loguru
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.dsl.schemas import ROOT_STREAM, RunContext, StreamID
from tracecat.identifiers import WorkspaceID
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
    "ctx_temporal_workspace_id",
    "with_temporal_workspace_id",
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
ctx_temporal_workspace_id: ContextVar[str | None] = ContextVar(
    "temporal-workspace-id", default=None
)
"""Explicit workspace scope override for Temporal payload encode/decode."""


@asynccontextmanager
async def with_session(session: AsyncSession):
    """Set the session in the context."""
    token = ctx_session.set(session)
    try:
        yield
    finally:
        ctx_session.reset(token)


@contextmanager
def with_temporal_workspace_id(workspace_id: WorkspaceID | str | None):
    """Temporarily override the workspace scope used for Temporal payload codecs."""
    token = ctx_temporal_workspace_id.set(
        None if workspace_id is None else str(workspace_id)
    )
    try:
        yield
    finally:
        ctx_temporal_workspace_id.reset(token)


def get_env() -> dict[str, str]:
    return ctx_env.get() or {}
