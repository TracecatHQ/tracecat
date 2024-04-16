"""Emit events for workflow and action runs.

Notes
-----
- This module contains functions to emit events for workflow and action runs.
- Events are emitted to the message queue and persisted in Lance/Tantivy

"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tracecat.auth import AuthenticatedAPIClient
from tracecat.contexts import ctx_mq_channel_pool, ctx_session_role
from tracecat.db import ActionRun as ActionRunEvent
from tracecat.db import WorkflowRun as WorkflowRunEvent
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
    role = ctx_session_role.get()
    time_now = datetime.now(UTC)
    event = WorkflowRunEvent(
        id=workflow_run_id,
        owner_id=role.user_id,
        created_at=time_now,
        updated_at=time_now,
        status="pending",
        # Exclude
        workflow_id=workflow_id,
    )
    async with AuthenticatedAPIClient() as client:
        response = await client.post(
            f"/workflows/{workflow_id}/runs",
            content=event.model_dump_json(exclude={"workflow_id"}),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload={"type": "workflow_run", **event.model_dump()},
    )
    logger.info(f"Emitted create workflow run event: {workflow_id=}")


async def emit_update_workflow_run_event(
    *,
    workflow_id: str,
    workflow_run_id: str,
    status: RunStatus,
) -> None:
    """Update a workflow run."""
    role = ctx_session_role.get()

    time_now = datetime.now(UTC)
    event = WorkflowRunEvent(
        id=workflow_run_id,
        owner_id=role.user_id,
        created_at=time_now,
        updated_at=time_now,
        status=status,
        # Exclude
        workflow_id=workflow_id,
    )
    async with AuthenticatedAPIClient() as client:
        response = await client.post(
            f"/workflows/{workflow_id}/runs/{workflow_run_id}",
            content=event.model_dump_json(exclude={"workflow_id"}),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 204:
            logger.error(
                f"Failed to update workflow run {workflow_run_id} with status {status}, {response.text}"
            )
    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload={"type": "workflow_run", **event.model_dump()},
    )
    logger.info(f"Emitted update workflow run event: {workflow_run_id=}, {status=}")


## Action Run Events


async def emit_create_action_run_event(action_run: ActionRun) -> None:
    """Create a workflow run."""
    action_id = action_run.action_id
    role = ctx_session_role.get()

    time_now = datetime.now(UTC)
    event = ActionRunEvent(
        id=action_run.id,
        owner_id=role.user_id,
        created_at=time_now,
        updated_at=time_now,
        status="pending",
        workflow_run_id=action_run.workflow_run_id,
        # Exclude
        action_id=action_id,
    )
    async with AuthenticatedAPIClient() as client:
        response = await client.post(
            f"/actions/{action_id}/runs",
            content=event.model_dump_json(exclude={"action_id"}),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload={"type": "action_run", **event.model_dump()},
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
    role = ctx_session_role.get()

    time_now = datetime.now(UTC)
    event = ActionRunEvent(
        id=action_run.id,
        owner_id=role.user_id,
        created_at=time_now,
        updated_at=time_now,
        status=status,
        workflow_run_id=action_run.workflow_run_id,
        error_msg=error_msg,
        result=result.model_dump_json() if result else None,
        # Exclude
        action_id=action_id,
    )
    async with AuthenticatedAPIClient() as client:
        response = await client.post(
            f"/actions/{action_id}/runs/{action_run.id}",
            # Explicitly serialize to json using pydantic to handle datetimes
            content=event.model_dump_json(exclude={"action_id"}),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 204:
            logger.error(
                f"Failed to update action run {action_run.id} in workflow run "
                f"{action_run.workflow_run_id} with status {status}"
            )

    await publish(
        pool=ctx_mq_channel_pool.get(),
        routing_keys=[role.user_id],
        payload={"type": "action_run", **event.model_dump()},
    )
    logger.info(f"Emitted update action run event: {action_run.id=}, {status=}.")
