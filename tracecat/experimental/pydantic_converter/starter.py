import asyncio
import logging
from datetime import datetime
from ipaddress import IPv4Address

from temporalio.client import Client

from tracecat.experimental.pydantic_converter.converter import pydantic_data_converter
from tracecat.experimental.pydantic_converter.worker import (
    MyPydanticModel,
    MyWorkflow,
)


async def main():
    logging.basicConfig(level=logging.INFO)
    # Connect client using the Pydantic converter
    client = await Client.connect(
        "localhost:7233", data_converter=pydantic_data_converter
    )

    # Run workflow
    result = await client.execute_workflow(
        MyWorkflow.run,
        [
            MyPydanticModel(
                some_ip=IPv4Address("127.0.0.1"),
                some_date=datetime(2000, 1, 2, 3, 4, 5),
            ),
            MyPydanticModel(
                some_ip=IPv4Address("127.0.0.2"),
                some_date=datetime(2001, 2, 3, 4, 5, 6),
            ),
        ],
        id="pydantic_converter-workflow-id",
        task_queue="pydantic_converter-task-queue",
    )
    logging.info("Got models from client: %s" % result)


if __name__ == "__main__":
    asyncio.run(main())
