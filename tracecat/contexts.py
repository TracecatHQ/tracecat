from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from pydantic import BaseModel

from tracecat import identifiers

if TYPE_CHECKING:
    from tracecat.types.auth import Role


class RunContext(BaseModel):
    wf_id: identifiers.WorkflowID
    wf_exec_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID
    wf_run_id: identifiers.WorkflowRunID


ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)
ctx_role: ContextVar[Role] = ContextVar("role", default=None)
ctx_logger: ContextVar[logging.Logger] = ContextVar("logger", default=None)
