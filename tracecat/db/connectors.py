from tracecat.db.schemas import Workflow
from tracecat.dsl.graph import RFGraph
from tracecat.dsl.workflow import DSLInput


def workflow_to_dsl(workflow: Workflow) -> DSLInput:
    # NOTE: Must only call inside a db session
    # Check that we're inside an open
    if not workflow.object:
        raise ValueError("Empty response object")
    if not workflow.actions:
        raise ValueError(
            "Empty actions list. Please hydrate the workflow by "
            "calling `workflow.actions` inside an open db session."
        )
    graph = RFGraph.from_workflow(workflow)
    actions = []
    for action in workflow.actions:
        action_inputs = action.inputs
        actions.append(
            {
                "ref": action.ref,
                "action": action.type,
                "args": action_inputs,
            }
        )
    return DSLInput(
        title=workflow.title,
        description=workflow.description,
        entrypoint=graph.entrypoint,
        actions=graph.action_statements(workflow),
        # config=workflow.config,
        # triggers=workflow.triggers,
        # inputs=workflow.inputs,
    )
