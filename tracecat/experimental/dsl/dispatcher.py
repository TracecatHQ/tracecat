import asyncio
import json
import sys
import uuid

from temporalio.client import Client

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.experimental.dsl._converter import pydantic_data_converter
from tracecat.experimental.dsl.workflow import DSLInput, DSLWorkflow
from tracecat.logging import standard_logger

logger = standard_logger("tracecat.experimental.dsl.dispatcher")


async def dispatch_wofklow(dsl: DSLInput) -> None:
    # Connect client
    role = ctx_role.get()
    client = await Client.connect(
        config.TEMPORAL__CLUSTER_URL, data_converter=pydantic_data_converter
    )

    # Run workflow
    logger.info(f"Executing DSL workflow: {dsl.title!r} {role=}")
    result = await client.execute_workflow(
        DSLWorkflow.run,
        dsl,
        id=f"dsl-workflow-{uuid.uuid4()}",
        task_queue="dsl-task-queue",
    )
    logger.info(f"Workflow result:\n{json.dumps(result, indent=2)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise RuntimeError("Expected single argument for YAML file")
    path = sys.argv[1]
    asyncio.run(dispatch_wofklow(DSLInput.from_yaml(path)))
