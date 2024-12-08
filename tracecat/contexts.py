from __future__ import annotations

from contextvars import ContextVar

import loguru
from pydantic import BaseModel

from tracecat.identifiers import WorkflowExecutionID, WorkflowID, WorkflowRunID
from tracecat.types.auth import Role


class RunContext(BaseModel):
    """This is the runtime context model for a workflow run. Passed into activities."""

    wf_id: WorkflowID
    wf_exec_id: WorkflowExecutionID
    wf_run_id: WorkflowRunID
    environment: str


ctx_run: ContextVar[RunContext] = ContextVar("run", default=None)
ctx_role: ContextVar[Role] = ContextVar("role", default=None)
ctx_logger: ContextVar[loguru.Logger] = ContextVar("logger", default=None)

SecretContextEnv = dict[str, dict[str, str]]
ctx_env: ContextVar[SecretContextEnv] = ContextVar("env", default={})
