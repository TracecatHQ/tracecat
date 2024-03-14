import { ActionType } from "@/types"
import { Session } from "@supabase/supabase-js"
import { ReactFlowInstance } from "reactflow"
import { z } from "zod"

import {
  ActionMetadata,
  actionMetadataSchema,
  ActionResponse,
  actionResponseSchema,
  WorkflowMetadata,
  workflowMetadataSchema,
  WorkflowResponse,
  workflowResponseSchema,
} from "@/types/schemas"
import { getAuthenticatedClient } from "@/lib/api"
import { BaseActionSchema } from "@/components/forms/action"

export async function updateDndFlow(
  maybeSession: Session | null,
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
    const client = getAuthenticatedClient(maybeSession)
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

export async function fetchWorkflow(
  maybeSession: Session | null,
  workflowId: string
): Promise<WorkflowResponse> {
  try {
    const client = getAuthenticatedClient(maybeSession)
    const response = await client.get<WorkflowResponse>(
      `/workflows/${workflowId}`
    )
    console.log("Workflow fetched successfully", response.data)
    return workflowResponseSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching workflow:", error)
    throw error
  }
}

export async function createWorkflow(
  maybeSession: Session | null,
  title: string,
  description: string = ""
): Promise<WorkflowMetadata> {
  const client = getAuthenticatedClient(maybeSession)
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
  console.log("Workflow created successfully", response.data)
  return workflowMetadataSchema.parse(response.data)
}

export async function fetchAllWorkflows(
  maybeSession: Session | null
): Promise<WorkflowMetadata[]> {
  try {
    const client = getAuthenticatedClient(maybeSession)
    const response = await client.get<WorkflowMetadata[]>("/workflows")
    let workflows = response.data

    console.log("Workflows fetched successfully", workflows)

    if (workflows.length === 0) {
      console.log("No workflows found. Creating a new one.")
      const newWorkflow = await createWorkflow(
        maybeSession,
        "My first workflow",
        "Welcome to Tracecat. This is your first workflow!"
      )
      workflows = [newWorkflow]
    }
    return z.array(workflowMetadataSchema).parse(workflows)
  } catch (error) {
    console.error("Error fetching workflows:", error)
    throw error
  }
}

export async function updateWorkflow(
  maybeSession: Session | null,
  workflowId: string,
  values: Object
) {
  const client = getAuthenticatedClient(maybeSession)
  const response = await client.post(`/workflows/${workflowId}`, values)
  return response.data
}

export async function getActionById(
  maybeSession: Session | null,
  actionId: string,
  workflowId: string
): Promise<ActionResponse> {
  try {
    const client = getAuthenticatedClient(maybeSession)
    const response = await client.get<ActionResponse>(`/actions/${actionId}`, {
      params: { workflow_id: workflowId },
    })
    return actionResponseSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching action:", error)
    throw error // Rethrow the error to ensure it's caught by useQuery's isError state
  }
}

// Form submission
export async function updateAction(
  maybeSession: Session | null,
  actionId: string,
  actionProps: BaseActionSchema & Record<string, any>
): Promise<ActionResponse> {
  const { title, description, ...inputs } = actionProps
  const inputsJson = JSON.stringify(inputs)
  const updateActionParams = {
    title,
    description,
    inputs: inputsJson,
  }

  const client = getAuthenticatedClient(maybeSession)
  const response = await client.post<ActionResponse>(
    `/actions/${actionId}`,
    JSON.stringify(updateActionParams),
    {
      headers: {
        "Content-Type": "application/json",
      },
    }
  )
  return actionResponseSchema.parse(response.data)
}

export async function deleteAction(
  maybeSession: Session | null,
  actionId: string
): Promise<void> {
  try {
    const client = getAuthenticatedClient(maybeSession)
    await client.delete(`/actions/${actionId}`)
    console.log(`Action with ID ${actionId} deleted successfully.`)
  } catch (error) {
    console.error(`Error deleting action with ID ${actionId}:`, error)
  }
}

export async function createAction(
  maybeSession: Session | null,
  type: ActionType,
  title: string,
  workflowId: string
): Promise<string | undefined> {
  try {
    const createActionMetadata = JSON.stringify({
      workflow_id: workflowId,
      type: type,
      title: title,
    })
    const client = getAuthenticatedClient(maybeSession)
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
  }
}
