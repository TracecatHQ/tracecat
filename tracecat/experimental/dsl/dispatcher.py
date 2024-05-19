import asyncio
import json
import sys
import uuid

import yaml
from loguru import logger
from temporalio.client import Client

from tracecat.experimental.dsl._converter import pydantic_data_converter
from tracecat.experimental.dsl.workflow import DSLInput, DSLWorkflow


async def dispatch_wofklow(dsl_yaml: str) -> None:
    # Convert the YAML to our dataclass structure. We use PyYAML + dacite to do
    # this but it can be done any number of ways.
    dsl_dict = yaml.safe_load(dsl_yaml)
    dsl_input = DSLInput.model_validate(dsl_dict)

    # Connect client
    client = await Client.connect(
        "http://host.docker.internal:7233", data_converter=pydantic_data_converter
    )

    # Run workflow
    result = await client.execute_workflow(
        DSLWorkflow.run,
        dsl_input,
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
    with open(sys.argv[1]) as yaml_file:
        dsl_yaml = yaml_file.read()

    # Run
    asyncio.run(dispatch_wofklow(dsl_yaml))
