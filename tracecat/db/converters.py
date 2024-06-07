from tracecat.db.schemas import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.dsl.graph import RFGraph


def workflow_to_dsl(workflow: Workflow) -> DSLInput:
    """Connector to convert an AppState workflow to a DSLInput."""
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
    return DSLInput(
        title=workflow.title,
        description=workflow.description,
        entrypoint=graph.entrypoint,
        actions=graph.action_statements(workflow),
        # config=workflow.config,
        # triggers=workflow.triggers,
        # inputs=workflow.inputs,
    )
