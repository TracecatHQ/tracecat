"""Emit events for workflow and action runs.

Notes
-----
- This module contains functions to emit events for workflow and action runs.
- Events are emitted to the message queue and persisted in Lance/Tantivy

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from tracecat.auth import AuthenticatedAPIClient
from tracecat.contexts import ctx_mq_channel_pool, ctx_session_role, ctx_workflow
from tracecat.logger import standard_logger
from tracecat.messaging import publish
from tracecat.types.api import RunStatus

if TYPE_CHECKING:
    from tracecat.runner.actions import ActionRun, ActionRunResult
logger = standard_logger(__name__)

## Workflow Run Events


async def emit_create_workflow_run_event(
    *, workflow_id: str, workflow_run_id: str
) -> None:
    """Create a workflow run."""
    event = WorkflowRunEvent(
        status="pending",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
    async with AuthenticatedAPIClient(http2=True) as client:
        response = await client.post(
            f"/workflows/{workflow_id}/runs",
            json=event.model_dump(include=["workflow_run_id"]),
        )
        response.raise_for_status()

    role = ctx_session_role.get()
    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload=event.model_dump(),
    )
    logger.info(f"Emitted create workflow run event: {workflow_id=}")


async def emit_update_workflow_run_event(
    *,
    workflow_id: str,
    workflow_run_id: str,
    status: RunStatus,
) -> None:
    """Update a workflow run."""
    event = WorkflowRunEvent(
        status=status,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
    async with AuthenticatedAPIClient(http2=True) as client:
        response = await client.post(
            f"/workflows/{workflow_id}/runs/{workflow_run_id}",
            json=event.model_dump(include=["status"]),
        )
        if response.status_code != 204:
            logger.error(
                f"Failed to update workflow run {workflow_run_id} with status {status}"
            )
    role = ctx_session_role.get()
    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload=event.model_dump(),
    )
    logger.info(f"Emitted update workflow run event: {workflow_run_id=}, {status=}")


## Action Run Events


async def emit_create_action_run_event(action_run: ActionRun) -> None:
    """Create a workflow run."""
    action_id = action_run.action_id
    workflow = ctx_workflow.get()
    event = ActionRunEvent(
        status="pending",
        action_id=action_id,
        action_key=action_run.action_key,
        action_run_id=action_run.id,
        workflow_id=workflow.id,
        workflow_run_id=action_run.workflow_run_id,
    )
    async with AuthenticatedAPIClient(http2=True) as client:
        response = await client.post(
            f"/actions/{action_id}/runs",
            json=event.model_dump(include=["action_run_id", "workflow_run_id"]),
        )
        response.raise_for_status()

    role = ctx_session_role.get()
    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload=event.model_dump(),
    )
    logger.info(f"Emitted create action run event: {action_run.id=}")


async def emit_update_action_run_event(
    action_run: ActionRun,
    *,
    status: RunStatus,
    error_msg: str | None = None,
    result: ActionRunResult | None = None,
) -> None:
    """Update a workflow run."""
    action_id = action_run.action_id
    workflow = ctx_workflow.get()

    event = ActionRunEvent(
        status=status,
        error_msg=error_msg,
        result=result.model_dump() if result else None,
        action_id=action_id,
        action_key=action_run.action_key,
        action_run_id=action_run.id,
        workflow_id=workflow.id,
        workflow_run_id=action_run.workflow_run_id,
    )
    async with AuthenticatedAPIClient(http2=True) as client:
        response = await client.post(
            f"/actions/{action_id}/runs/{action_run.id}",
            # Explicitly serialize to json using pydantic to handle datetimes
            content=event.model_dump_json(include=["status", "error_msg", "result"]),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 204:
            logger.error(
                f"Failed to update action run {action_run.id} in workflow run "
                f"{action_run.workflow_run_id} with status {status}"
            )

    role = ctx_session_role.get()
    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload=event.model_dump(),
    )
    logger.info(f"Emitted update action run event: {action_run.id=}, {status=}.")


class RunnerEvent(BaseModel):
    # A MQ consumer can filter events based on the type
    type: str


class ActionRunEvent(RunnerEvent):
    type: Literal["action_run"] = Field("action_run", frozen=True)
    # Event
    status: RunStatus
    error_msg: str | None = None
    result: dict[str, Any] | None = None
    # Context
    action_id: str
    action_key: str
    action_run_id: str
    workflow_id: str
    workflow_run_id: str


class WorkflowRunEvent(RunnerEvent):
    type: Literal["workflow_run"] = Field("workflow_run", frozen=True)
    # Event
    status: RunStatus
    # Context
    workflow_id: str
    workflow_run_id: str
