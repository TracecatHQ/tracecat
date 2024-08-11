import { workflowsUpdateWorkflow } from "@/client"
import { ReactFlowInstance } from "reactflow"

import {
  actionMetadataSchema,
  actionSchema,
  type Action,
  type ActionMetadata,
  type WorkflowMetadata,
} from "@/types/schemas"
import { client } from "@/lib/api"
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

export async function deleteWorkflow(workflowId: string): Promise<void> {
  try {
    await client.delete(`/workflows/${workflowId}`)
    console.log(`Workflow with ID ${workflowId} deleted successfully.`)
  } catch (error) {
    console.error(`Error deleting workflow with ID ${workflowId}:`, error)
  }
}

export async function getActionById(
  actionId: string,
  workflowId: string
): Promise<Action> {
  try {
    const response = await client.get<Action>(`/actions/${actionId}`, {
      params: { workflow_id: workflowId },
    })
    return actionSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching action:", error)
    throw error // Rethrow the error to ensure it's caught by useQuery's isError state
  }
}

// Form submission
export async function updateAction(
  actionId: string,
  actionProps: Record<string, unknown>
): Promise<Action> {
  const { title, description, inputs } = actionProps
  const updateActionParams = {
    title,
    description,
    inputs,
  }

  const response = await client.post<Action>(
    `/actions/${actionId}`,
    JSON.stringify(updateActionParams),
    {
      headers: {
        "Content-Type": "application/json",
      },
    }
  )
  return actionSchema.parse(response.data)
}

export async function deleteAction(actionId: string): Promise<void> {
  try {
    await client.delete(`/actions/${actionId}`)
    console.log(`Action with ID ${actionId} deleted successfully.`)
  } catch (error) {
    console.error(`Error deleting action with ID ${actionId}:`, error)
  }
}

export async function createAction(
  type: string,
  title: string,
  workflowId: string
): Promise<string> {
  try {
    const createActionMetadata = JSON.stringify({
      workflow_id: workflowId,
      type: type,
      title: title,
    })

    const response = await client.post<ActionMetadata>(
      "/actions",
      createActionMetadata,
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    console.log("Action created successfully:", response.data)
    const validatedResponse = actionMetadataSchema.parse(response.data)
    return validatedResponse.id
  } catch (error) {
    console.error("Error creating action:", error)
    throw error
  }
}

/**
 *
 * View all playbooks.
 *
 * @param maybeToken
 * @returns
 */
export async function fetchAllPlaybooks(): Promise<WorkflowMetadata[]> {
  try {
    const response = await client.get("/workflows?library=true")
    return response.data
  } catch (error) {
    console.error("Error fetching playbooks:", error)
    throw error
  }
}
