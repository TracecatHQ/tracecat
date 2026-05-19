"""ExecutorWorker - Temporal worker for action execution and registry sync.

This worker listens on the 'shared-action-queue' and executes:
- Actions dispatched from DSL workflows
- Registry sync operations (when sandboxed sync is enabled)

Supported backends (via TRACECAT__EXECUTOR_BACKEND):
- pool: Warm nsjail workers (single-tenant, high throughput)
- ephemeral: Cold nsjail subprocess per action (multitenant, full isolation)
- direct: Direct subprocess execution
- test: In-process execution (tests only)
- auto: Auto-select based on environment

Architecture:
    DSLWorkflow (tracecat-task-queue)
        |
        v
    workflow.execute_activity("execute_action_activity", task_queue="shared-action-queue")
        |
        v
    ExecutorWorker picks up task
        |
        v
    dispatch_action() -> backend.execute() -> action result

    API Service (registry sync)
        |
        v
    workflow.execute_workflow(RegistrySyncWorkflow, task_queue="shared-action-queue")
        |
        v
    ExecutorWorker picks up workflow
        |
        v
    RegistrySyncRunner -> git clone, install, discover, tarball
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio import workflow
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

with workflow.unsafe.imports_passed_through():
    import uvloop

    from tracecat import config
    from tracecat.dsl.client import get_temporal_client
    from tracecat.executor.action_gateway.server import ActionGateway
    from tracecat.executor.activities import ExecutorActivities
    from tracecat.executor.backends import (
        initialize_executor_backend,
        shutdown_executor_backend,
    )
    from tracecat.logger import logger
    from tracecat.registry.sync.workflow import (
        RegistryArtifactsBackfillWorkflow,
        RegistrySyncActivities,
        RegistrySyncWorkflow,
    )
    from tracecat.temporal.worker_lifecycle import run_worker_entrypoint

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def new_sandbox_runner() -> SandboxedWorkflowRunner:
    """Create a sandboxed workflow runner with relaxed restrictions.

    The RegistrySyncWorkflow imports schemas that transitively pull in:
    - FastAPI → anyio → sniffio (ThreadLocal subclass issues)
    - SQLAlchemy (metaclass/annotation issues)
    - Pydantic (validation complexity)

    Since RegistrySyncWorkflow is a simple activity wrapper with no complex
    determinism requirements, we use a permissive sandbox configuration.
    """
    # Relax datetime restrictions (same as DSL worker)
    invalid_module_member_children = dict(
        SandboxRestrictions.invalid_module_members_default.children
    )
    del invalid_module_member_children["datetime"]

    return SandboxedWorkflowRunner(
        restrictions=dataclasses.replace(
            SandboxRestrictions.default,
            invalid_module_members=dataclasses.replace(
                SandboxRestrictions.invalid_module_members_default,
                children=invalid_module_member_children,
            ),
        )
    )


async def main(shutdown_event: asyncio.Event | None = None) -> None:
    """Run the ExecutorWorker."""
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    # Get configuration
    task_queue = config.TRACECAT__EXECUTOR_QUEUE
    max_concurrent = int(
        os.environ.get("TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES", 100)
    )
    threadpool_max_workers = int(
        os.environ.get("TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS", 100)
    )

    logger.info(
        "Starting ExecutorWorker",
        task_queue=task_queue,
        max_concurrent_activities=max_concurrent,
        threadpool_max_workers=threadpool_max_workers,
        executor_backend=config.TRACECAT__EXECUTOR_BACKEND,
    )
    action_gateway = ActionGateway()

    try:
        # Start the local action gateway before sandbox workers are spawned so its
        # socket path is available in their immutable process environment.
        await action_gateway.start()

        # Initialize the executor backend before accepting tasks
        await initialize_executor_backend()

        client = await get_temporal_client()

        # Collect all activities from executor and registry sync
        activities = [
            *ExecutorActivities.get_activities(),
            *RegistrySyncActivities.get_activities(),
        ]

        # Collect all workflows
        workflows = [RegistrySyncWorkflow, RegistryArtifactsBackfillWorkflow]

        logger.debug(
            "Activities loaded",
            activities=[
                getattr(a, "__temporal_activity_definition").name for a in activities
            ],
        )
        logger.debug(
            "Workflows loaded",
            workflows=[w.__name__ for w in workflows],
        )

        with ThreadPoolExecutor(max_workers=threadpool_max_workers) as executor:
            async with Worker(
                client,
                task_queue=task_queue,
                workflows=workflows,
                activities=activities,
                activity_executor=executor,
                max_concurrent_activities=max_concurrent,
                workflow_runner=new_sandbox_runner(),
                graceful_shutdown_timeout=timedelta(minutes=5),
            ):
                logger.info(
                    "ExecutorWorker started, ctrl+c to exit",
                    task_queue=task_queue,
                    max_concurrent_activities=max_concurrent,
                    num_workflows=len(workflows),
                    num_activities=len(activities),
                )
                await shutdown_event.wait()
                logger.info("ExecutorWorker shutdown requested")
            logger.info("Temporal Worker context exited")
    finally:
        logger.info("Shutting down executor backend")
        await shutdown_executor_backend()
        await action_gateway.stop()


if __name__ == "__main__":
    run_worker_entrypoint(main)
