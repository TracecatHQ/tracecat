from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta
from ipaddress import IPv4Address

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

# We always want to pass through external modules to the sandbox that we know
# are safe for workflow use
with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel

    from tracecat.experimental.pydantic_converter.converter import (
        pydantic_data_converter,
    )


class MyPydanticModel(BaseModel):
    some_ip: IPv4Address
    some_date: datetime


# MyModelList = list[MyPydanticModel]


@activity.defn
async def my_activity(models: list[MyPydanticModel]) -> list[MyPydanticModel]:
    activity.logger.info("Got models in activity: %s" % models)
    return models


@workflow.defn
class MyWorkflow:
    @workflow.run
    async def run(self, models: list[MyPydanticModel]) -> list[MyPydanticModel]:
        workflow.logger.info("Got models in workflow: %s" % models)
        return await workflow.execute_activity(
            my_activity, models, start_to_close_timeout=timedelta(minutes=1)
        )


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


async def main():
    logging.basicConfig(level=logging.INFO)
    # Connect client using the Pydantic converter
    client = await Client.connect(
        "localhost:7233", data_converter=pydantic_data_converter
    )

    # Run a worker for the workflow
    async with Worker(
        client,
        task_queue="pydantic_converter-task-queue",
        workflows=[MyWorkflow],
        activities=[my_activity],
        workflow_runner=new_sandbox_runner(),
    ):
        # Wait until interrupted
        print("Worker started, ctrl+c to exit")
        await interrupt_event.wait()
        print("Shutting down")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        interrupt_event.set()
        loop.run_until_complete(loop.shutdown_asyncgens())
