from typing import Annotated, Any

from pydantic import Field
from sqlmodel import Session

from tracecat.contexts import ctx_role, ctx_run
from tracecat.db.engine import create_db_engine
from tracecat.dsl.common import DSLInput
from tracecat.dsl.workflow import DSLRunArgs
from tracecat.registry import registry
from tracecat.workflow.service import WorkflowDefinitionsService


@registry.register(
    namespace="core.workflow",
    version="0.1.0",
    description="Execute a child workflow. The child workflow inherits the parent's execution context.",
    default_title="Execute Child Workflow",
    display_group="Workflows",
)
async def execute(
    workflow_title: Annotated[
        str,
        Field(
            ...,
            description=("The title of the child workflow. "),
        ),
    ],
    trigger_inputs: Annotated[
        Any,
        Field(
            ...,
            description="The inputs to pass to the child workflow.",
        ),
    ],
    version: Annotated[
        int | None,
        Field(..., description="The version of the child workflow definition, if any."),
    ] = None,
) -> DSLRunArgs:
    # 1. Grab the child workflow DSL
    engine = create_db_engine()
    role = ctx_role.get()
    with Session(engine) as session:
        service = WorkflowDefinitionsService(session, role=role)
        defn = service.get_definition_by_workflow_title(workflow_title, version=version)

    # 2. Set inputs in the DSL (TRIGGER)

    dsl = DSLInput(**defn.content)
    parent_run_context = ctx_run.get()
    return DSLRunArgs(
        role=role,
        dsl=dsl,
        wf_id=defn.workflow_id,
        parent_run_context=parent_run_context,
        trigger_inputs=trigger_inputs,
    )
