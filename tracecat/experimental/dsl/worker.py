import asyncio
import dataclasses

from loguru import logger
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

# We always want to pass through external modules to the sandbox that we know
# are safe for workflow use
with workflow.unsafe.imports_passed_through():
    from tracecat import config
    from tracecat.experimental.dsl._converter import pydantic_data_converter
    from tracecat.experimental.dsl.workflow import DSLWorkflow, dsl_activities
    from tracecat.experimental.registry import registry


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


async def main() -> None:
    # Connect client
    logger.info("Connecting to Temporal")

    registry.init()
    client = await Client.connect(
        config.TEMPORAL__CLUSTER_URL, data_converter=pydantic_data_converter
    )

    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue="dsl-task-queue",
        activities=dsl_activities,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        # Wait until interrupted
        logger.info("Worker started, ctrl+c to exit")
        await interrupt_event.wait()
        logger.info("Shutting down")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
        loop.run_until_complete(loop.shutdown_asyncgens())
