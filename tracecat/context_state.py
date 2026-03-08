from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from tracecat.auth.types import Role

ctx_run: ContextVar[Any | None] = ContextVar("run", default=None)
ctx_role: ContextVar[Role | None] = ContextVar("role", default=None)
ctx_request_id: ContextVar[str | None] = ContextVar("request-id", default=None)
ctx_log_masks: ContextVar[tuple[str, ...]] = ContextVar("log-masks", default=())
ctx_session_id: ContextVar[uuid.UUID | None] = ContextVar("session-id", default=None)
