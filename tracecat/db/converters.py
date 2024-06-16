from tracecat import identifiers
from tracecat.db.schemas import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.dsl.graph import RFEdge, RFGraph, UDFNode, UDFNodeData
from tracecat.logging import logger


def workflow_to_dsl(workflow: Workflow) -> DSLInput:
    """Converter for Workflow to DSLInput.

    Use Case: Committing a Workflow into a Workflow Definition
    """
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
        entrypoint=graph.logical_entrypoint.ref,
        actions=graph.action_statements(workflow),
        # config=workflow.config,
        # triggers=workflow.triggers,
        # inputs=workflow.inputs,
    )


# For syncing headless to the frontend, we need to convert the DSLInput into an RFGraph that we can then convert to a Workflow object.
# We need DSLInputs (yaml) to show changes in the frontend graph object


def dsl_to_graph(workflow: Workflow, dsl: DSLInput) -> RFGraph:
    """Converter for DSLInput to Workflow.

    Use Case: Syncing headless to the frontend.
    Call this only
    """
    if not dsl.actions:
        raise ValueError("Empty actions list")
    wf_id = workflow.id
    graph = RFGraph.from_workflow(workflow)
    trigger = graph.trigger

    # Create nodes and edges
    nodes: list[RFEdge] = [trigger]
    edges: list[RFEdge] = []
    try:
        for action in dsl.actions:
            # Get updated nodes
            dst_key = identifiers.action.key(wf_id, action.ref)
            node = UDFNode(
                id=dst_key,
                data=UDFNodeData(
                    title=action.title,
                    type=action.action,
                ),
            )
            nodes.append(node)

            for src_ref in action.depends_on:
                src_key = identifiers.action.key(wf_id, src_ref)
                edges.append(RFEdge(source=src_key, target=dst_key))

        entrypoint_id = identifiers.action.key(wf_id, dsl.entrypoint)
        # Add trigger edge
        edges.append(
            RFEdge(source=trigger.id, target=entrypoint_id, label="⚡ Trigger")
        )
        return RFGraph(nodes=nodes, edges=edges)
    except Exception as e:
        logger.opt(exception=e).error("Error creating graph")
        raise e


#
