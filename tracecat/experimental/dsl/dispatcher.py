import asyncio
import json
import os
import sys
import uuid

from pydantic import BaseModel

from tracecat.contexts import ctx_role
from tracecat.experimental.dsl.common import get_temporal_client
from tracecat.experimental.dsl.workflow import DSLContext, DSLInput, DSLWorkflow
from tracecat.logging import standard_logger

logger = standard_logger("tracecat.experimental.dsl.dispatcher")


class DispatchResult(BaseModel):
    wf_id: str
    final_context: DSLContext


async def dispatch_workflow(dsl: DSLInput, **kwargs) -> DispatchResult:
    # Connect client
    role = ctx_role.get()
    client = await get_temporal_client()
    # Run workflow
    logger.info(f"Executing DSL workflow: {dsl.title!r} {role=}")
    wf_id = f"dsl-workflow-{uuid.uuid4()}"
    result = await client.execute_workflow(
        DSLWorkflow.run,
        dsl,
        id=wf_id,
        task_queue=os.environ.get("TEMPORAL__CLUSTER_QUEUE", "dsl-task-queue"),
        **kwargs,
    )
    logger.debug(f"Workflow result:\n{json.dumps(result, indent=2)}")
    return DispatchResult(wf_id=wf_id, final_context=result)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise RuntimeError("Expected single argument for YAML file")
    path = sys.argv[1]
    asyncio.run(dispatch_workflow(DSLInput.from_yaml(path)))
