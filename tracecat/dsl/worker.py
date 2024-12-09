import asyncio
import dataclasses
import os
from collections.abc import Callable
from typing import Any

from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from tracecat.dsl.action import DSLActivities
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.validation import validate_trigger_inputs_activity
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.logger import logger
from tracecat.store.service import store_workflow_result_activity
from tracecat.workflow.management.definitions import (
    get_workflow_definition_activity,
)
from tracecat.workflow.schedules.service import WorkflowSchedulesService


# Due to known issues with Pydantic's use of issubclass and our inability to
# override the check in sandbox, Pydantic will think datetime is actually date
# in the sandbox. At the expense of protecting against datetime.now() use in
# workflows, we're going to remove datetime module restrictions. See sdk-python
# README's discussion of known sandbox issues for more details.
def new_sandbox_runner() -> SandboxedWorkflowRunner:
    # TODO(cretz): Use with_child_unrestricted when https://github.com/temporalio/sdk-python/issues/254
    # is fixed and released
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


interrupt_event = asyncio.Event()


def get_worker_activities() -> list[Callable[..., Any]]:
    return [
        *DSLActivities.load(),
        *WorkflowSchedulesService.get_activities(),
        get_workflow_definition_activity,
        validate_trigger_inputs_activity,
        store_workflow_result_activity,
    ]


async def main() -> None:
    client = await get_temporal_client()

    # Run a worker for the activities and workflow
    activities = get_worker_activities()
    logger.debug(
        "Activities loaded",
        activities=[
            getattr(a, "__temporal_activity_definition").name for a in activities
        ],
    )
    async with Worker(
        client,
        task_queue=os.environ.get("TEMPORAL__CLUSTER_QUEUE", "tracecat-task-queue"),
        activities=activities,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        logger.info("Worker started, ctrl+c to exit")
        # Wait until interrupted
        await interrupt_event.wait()
        logger.info("Shutting down")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
        loop.run_until_complete(loop.shutdown_asyncgens())
