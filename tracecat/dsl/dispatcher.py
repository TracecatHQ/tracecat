import asyncio
import json
import os
import sys
from typing import Any

from loguru import logger
from pydantic import BaseModel

from tracecat import identifiers
from tracecat.contexts import ctx_role
from tracecat.dsl.common import DSLInput, get_temporal_client
from tracecat.dsl.workflow import DSLContext, DSLRunArgs, DSLWorkflow


class DispatchResult(BaseModel):
    wf_id: identifiers.WorkflowID
    final_context: DSLContext


async def dispatch_workflow(
    dsl: DSLInput, wf_id: identifiers.WorkflowID, **kwargs: Any
) -> DispatchResult:
    # Connect client
    role = ctx_role.get()
    wf_exec_id = identifiers.workflow.exec_id(wf_id)
    logger.info(
        f"Executing DSL workflow: {dsl.title}", role=role, wf_exec_id=wf_exec_id
    )
    client = await get_temporal_client()
    # Run workflow
    result = await client.execute_workflow(
        DSLWorkflow.run,
        DSLRunArgs(dsl=dsl, role=role, wf_id=wf_id),
        id=wf_exec_id,
        task_queue=os.environ.get("TEMPORAL__CLUSTER_QUEUE", "tracecat-task-queue"),
        **kwargs,
    )
    logger.debug(f"Workflow result:\n{json.dumps(result, indent=2)}")
    return DispatchResult(wf_id=wf_id, final_context=result)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise RuntimeError("Expected single argument for YAML file")
    path = sys.argv[1]
    asyncio.run(dispatch_workflow(DSLInput.from_yaml(path)))
