import asyncio
import json
import sys
import uuid

from temporalio.client import Client

from tracecat import config
from tracecat.experimental.dsl._converter import pydantic_data_converter
from tracecat.experimental.dsl.workflow import DSLInput, DSLWorkflow
from tracecat.logging import standard_logger

logger = standard_logger("tracecat.experimental.dsl.dispatcher")


async def dispatch_wofklow(dsl: DSLInput) -> None:
    # Connect client
    client = await Client.connect(
        config.TEMPORAL__CLUSTER_URL, data_converter=pydantic_data_converter
    )

    # Run workflow
    result = await client.execute_workflow(
        DSLWorkflow.run,
        dsl,
        id=f"dsl-workflow-id-{uuid.uuid4()}",
        task_queue="dsl-task-queue",
    )
    logger.info(f"Workflow result:\n{json.dumps(result, indent=2)}")


if __name__ == "__main__":
    # Require the YAML file as an argument. We read this _outside_ of the async
    # def function because thread-blocking IO should never happen in async def
    # functions.
    if len(sys.argv) != 2:
        raise RuntimeError("Expected single argument for YAML file")
    path = sys.argv[1]

    # Run
    asyncio.run(dispatch_wofklow(DSLInput.from_yaml(path)))
