from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aio_pika import Channel
    from aio_pika.pool import Pool

    from tracecat.auth import Role
    from tracecat.runner.actions import ActionRun
    from tracecat.runner.workflows import Workflow
    from tracecat.types.workflow import WorkflowRunContext


ctx_role: ContextVar[Role] = ContextVar("role", default=None)
# TODO: Deprecate this contextvar
ctx_workflow: ContextVar[Workflow] = ContextVar("workflow", default=None)
ctx_workflow_run: ContextVar[WorkflowRunContext] = ContextVar(
    "workflow_run", default=None
)
ctx_action_run: ContextVar[ActionRun] = ContextVar("action_run", default=None)
ctx_mq_channel_pool: ContextVar[Pool[Channel]] = ContextVar(
    "mq_channel_pool", default=None
)
ctx_logger: ContextVar[logging.Logger] = ContextVar("logger", default=None)
