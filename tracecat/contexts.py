from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from tracecat.auth import Role


class RunContext(BaseModel):
    wf_id: str
    wf_run_id: str


ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)
ctx_role: ContextVar[Role] = ContextVar("role", default=None)
ctx_logger: ContextVar[logging.Logger] = ContextVar("logger", default=None)
