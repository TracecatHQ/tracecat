import { actionsCreateAction, workflowsUpdateWorkflow } from "@/client"
import { ReactFlowInstance } from "reactflow"

import { isEphemeral } from "@/components/workbench/canvas/canvas"

export async function updateWorkflowGraphObject(
  workspaceId: string,
  workflowId: string,
  reactFlowInstance: ReactFlowInstance
) {
  try {
    const object = reactFlowInstance.toObject()

    // Filter out non-ephemeral nodes and their associated edges
    const ephemeralNodeIds = new Set(
      object.nodes.filter(isEphemeral).map((node) => node.id)
    )

    // Keep nodes that are NOT ephemeral
    object.nodes = object.nodes.filter((node) => !ephemeralNodeIds.has(node.id))
    // Keep edges that are NOT connected to ephemeral nodes
    object.edges = object.edges.filter(
      (edge) => !ephemeralNodeIds.has(edge.target)
    )

    // Check that the object at least contains the trigger node
    if (!object.nodes.some((node) => node.type === "trigger")) {
      throw new Error("Workflow cannot be saved without a trigger node")
    }

    await workflowsUpdateWorkflow({
      workspaceId,
      workflowId,
      requestBody: {
        object,
      },
    })
  } catch (error) {
    console.error("Error updating DnD flow object:", error)
  }
}

export async function createAction(
  type: string,
  title: string,
  workflowId: string,
  workspaceId: string
): Promise<string> {
  try {
    const actionMetadata = await actionsCreateAction({
      workspaceId,
      requestBody: {
        workflow_id: workflowId,
        type: type,
        title: title,
      },
    })
    return actionMetadata.id
  } catch (error) {
    console.error("Error creating action:", error)
    throw error
  }
}
