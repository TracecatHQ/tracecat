from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel

from tracecat import config, identifiers
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.workflow import DSLContext, DSLRunArgs, DSLWorkflow

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput


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
        task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        **kwargs,
    )
    # Write result to file for debugging
    if os.getenv("DUMP_TRACECAT_RESULT", "0") in ("1", "true"):
        path = config.TRACECAT__EXECUTIONS_DIR / f"{wf_exec_id}.json"
        path.touch()
        with path.open("w") as f:
            json.dump(result, f, indent=2)
    else:
        logger.debug(f"Workflow result:\n{json.dumps(result, indent=2)}")
    return DispatchResult(wf_id=wf_id, final_context=result)
