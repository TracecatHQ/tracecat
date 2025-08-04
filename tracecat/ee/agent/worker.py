import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from tracecat import config
from tracecat.dsl._converter import get_data_converter
from tracecat.ee.agent.workflow import AgenticLoopWorkflow, model_request
from tracecat.logger import logger


async def main():
    # Connect to Temporal server
    client = await Client.connect("localhost:7233", data_converter=get_data_converter())

    # Create a worker that hosts the workflow
    worker = Worker(
        client,
        task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        workflows=[AgenticLoopWorkflow],
        activities=[model_request],
    )

    # Run the worker
    logger.info("Worker started", task_queue=config.TEMPORAL__CLUSTER_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
