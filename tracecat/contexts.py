from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aio_pika import Channel
    from aio_pika.pool import Pool

    from tracecat.auth import Role
    from tracecat.runner.workflows import Workflow

ctx_session_role: ContextVar[Role] = ContextVar("session_role", default=None)
ctx_workflow: ContextVar[Workflow] = ContextVar("workflow", default=None)
ctx_mq_channel_pool: ContextVar[Pool[Channel]] = ContextVar(
    "mq_channel_pool", default=None
)
