"""
Simple Temporal workflow example with activity and interceptor.
Requires temporalio package.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
    Worker,
)

with workflow.unsafe.imports_passed_through():
    from tracecat.logger import logger


# Activity Definition
@dataclass
class NumberProcessingResult:
    """Result of number processing activity."""

    input_number: int
    result: int


@activity.defn
async def process_number(number: int) -> Any:
    """Simple activity that doubles a number."""
    result = number * 2
    return NumberProcessingResult(input_number=number, result=result)


# Workflow Definition
@workflow.defn
class NumberProcessingWorkflow:
    """Workflow that processes a number using an activity."""

    @workflow.run
    async def run(self, number: int) -> Any:
        """Execute the number processing workflow."""
        return await workflow.execute_activity(
            process_number,
            number,
            start_to_close_timeout=timedelta(seconds=10),
        )


# Activity Interceptor
class ObjectStoreActivityInterceptor(ActivityInboundInterceptor):
    """Interceptor that writes activity results to the object store."""

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        """Execute activity and log its result."""
        # You can use this pattern to get the activity type
        print(activity.info())
        print("Executing activity", input.headers, input.fn.__name__)
        # Execute the activity
        result = await self.next.execute_activity(input)

        # Log the result
        logger.info("In sandbox", in_sandbox=workflow.unsafe.in_sandbox())
        if isinstance(result, NumberProcessingResult):
            print(
                f"Activity completed - Input: {result.input_number}, "
                f"Result: {result.result}"
            )
        # We're able to return anything we want here
        # Note that this shows up as "Hello" in the workflow history
        # We could just write the result
        return "Hello"


class ObjectStoreInterceptor(Interceptor):
    """Main interceptor implementation."""

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        """Return the activity interceptor."""
        return ObjectStoreActivityInterceptor(next)


# Example usage
async def main() -> None:
    """Run the workflow example."""
    # Create client
    client = await Client.connect("localhost:7233")

    # Run worker
    worker = Worker(
        client,
        task_queue="number-processing-task-queue",
        workflows=[NumberProcessingWorkflow],
        activities=[process_number],
        interceptors=[ObjectStoreInterceptor()],
    )

    async with worker:
        # Start workflow
        result = await client.execute_workflow(
            NumberProcessingWorkflow.run,
            1,
            id="number-processing-workflow",
            task_queue="number-processing-task-queue",
        )

        print(f"Workflow completed - Final result: {result}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
