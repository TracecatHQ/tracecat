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
import signal
from concurrent.futures import ThreadPoolExecutor

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
    from tracecat.executor.activities import ExecutorActivities
    from tracecat.executor.backends import (
        initialize_executor_backend,
        shutdown_executor_backend,
    )
    from tracecat.logger import logger
    from tracecat.registry.sync.workflow import (
        RegistrySyncActivities,
        RegistrySyncWorkflow,
    )

interrupt_event = asyncio.Event()

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


async def main() -> None:
    """Run the ExecutorWorker."""
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

    # Initialize the executor backend before accepting tasks
    await initialize_executor_backend()

    try:
        client = await get_temporal_client()

        # Collect all activities from executor and registry sync
        activities = [
            *ExecutorActivities.get_activities(),
            *RegistrySyncActivities.get_activities(),
        ]

        # Collect all workflows
        workflows = [RegistrySyncWorkflow]

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
            ):
                logger.info(
                    "ExecutorWorker started, ctrl+c to exit",
                    task_queue=task_queue,
                    max_concurrent_activities=max_concurrent,
                    num_workflows=len(workflows),
                    num_activities=len(activities),
                )
                # Wait until interrupted
                await interrupt_event.wait()
                logger.info("Shutting down ExecutorWorker")
    finally:
        logger.info("Shutting down executor backend")
        await shutdown_executor_backend()


def _signal_handler(sig: int, _frame: object) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("Received shutdown signal", signal=sig)
    interrupt_event.set()


if __name__ == "__main__":
    # Install signal handlers before starting the event loop
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
