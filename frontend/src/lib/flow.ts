import { ReactFlowInstance } from "reactflow"
import { z } from "zod"

import {
  actionMetadataSchema,
  actionSchema,
  NodeType,
  workflowMetadataSchema,
  WorkflowRun,
  workflowRunSchema,
  workflowSchema,
  type Action,
  type ActionMetadata,
  type Workflow,
  type WorkflowMetadata,
} from "@/types/schemas"
import { client } from "@/lib/api"
import type { BaseActionForm } from "@/components/workspace/panel/action/schemas"

export async function updateDndFlow(
  workflowId: string,
  reactFlowInstance: ReactFlowInstance | null
) {
  try {
    const objectContent = reactFlowInstance
      ? reactFlowInstance.toObject()
      : null
    const updateFlowObjectParams = JSON.stringify({
      object: JSON.stringify(objectContent),
    })

    await client.post(`/workflows/${workflowId}`, updateFlowObjectParams, {
      headers: {
        "Content-Type": "application/json",
      },
    })
    console.log("Updated DnD flow object")
  } catch (error) {
    console.error("Error updating DnD flow object:", error)
  }
}

export async function fetchWorkflow(workflowId: string): Promise<Workflow> {
  try {
    const response = await client.get<Workflow>(`/workflows/${workflowId}`)
    return workflowSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching workflow:", error)
    throw error
  }
}

export async function createWorkflow(
  title: string,
  description: string = ""
): Promise<WorkflowMetadata> {
  const response = await client.post<WorkflowMetadata>(
    "/workflows",
    JSON.stringify({
      title,
      description,
    }),
    {
      headers: {
        "Content-Type": "application/json",
      },
    }
  )
  return workflowMetadataSchema.parse(response.data)
}

export async function fetchAllWorkflows(): Promise<WorkflowMetadata[]> {
  try {
    const response = await client.get<WorkflowMetadata[]>("/workflows")
    let workflows = response.data

    return z.array(workflowMetadataSchema).parse(workflows)
  } catch (error) {
    console.error("Error fetching workflows:", error)
    throw error
  }
}

export async function updateWorkflow(workflowId: string, values: Object) {
  const response = await client.post(`/workflows/${workflowId}`, values)
  return response.data
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

type ActionProps = (BaseActionForm & Record<string, any>) | Record<string, any>
// Form submission
export async function updateAction(
  actionId: string,
  actionProps: ActionProps
): Promise<Action> {
  const { title, description, ...inputs } = actionProps
  const inputsJson = JSON.stringify(inputs)
  const updateActionParams = {
    title,
    description,
    inputs: inputsJson,
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
  type: NodeType,
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

export async function triggerWorkflow(
  workflowId: string,
  actionKey: string,
  payload: Record<string, any>
) {
  try {
    const response = await client.post(
      `/workflows/${workflowId}/trigger`,
      JSON.stringify({
        action_key: actionKey,
        payload,
      }),
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    if (response.status !== 200) {
      throw new Error("Failed to trigger workflow")
    }
    console.log("Workflow triggered successfully")
  } catch (error) {
    console.error("Error triggering workflow:", error)
  }
}

export async function fetchWorkflowRuns(
  workflowId: string
): Promise<WorkflowRun[]> {
  try {
    const response = await client.get<WorkflowRun[]>(
      `/workflows/${workflowId}/runs`
    )
    return z.array(workflowRunSchema).parse(response.data)
  } catch (error) {
    console.error("Error fetching workflow runs:", error)
    throw error
  }
}

export async function fetchWorkflowRun(
  workflowId: string,
  workflowRunId: string
): Promise<WorkflowRun> {
  try {
    const response = await client.get<WorkflowRun>(
      `/workflows/${workflowId}/runs/${workflowRunId}`
    )
    return workflowRunSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching workflow runs:", error)
    throw error
  }
}

/**
 *
 * To add a workflow from Playbooks,
 *
 * @param maybeToken
 * @param workflowId
 * @returns
 */
export async function addPlaybook(workflowId: string) {
  try {
    const response = await client.post(`/workflows/${workflowId}/copy`)
    return response.data
  } catch (error) {
    console.error("Error adding integration:", error)
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
    console.error("Error adding integration:", error)
    throw error
  }
}
