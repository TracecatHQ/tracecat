import { Session } from "@supabase/supabase-js"
import axios from "axios"
import { ReactFlowInstance } from "reactflow"
import { z } from "zod"

import { WorkflowMetadata, workflowMetadataSchema } from "@/types/schemas"
import { getAuthenticatedClient } from "@/lib/api"

export async function saveFlow(
  session: Session,
  workflowId: string,
  reactFlowInstance: ReactFlowInstance
) {
  if (!workflowId || !reactFlowInstance) return

  try {
    const flowObject = reactFlowInstance.toObject()
    const updateFlowObjectParams = JSON.stringify({
      object: JSON.stringify(flowObject),
    })
    const client = getAuthenticatedClient(session)
    await client.post(`/workflows/${workflowId}`, updateFlowObjectParams, {
      headers: {
        "Content-Type": "application/json",
      },
    })
  } catch (error) {
    console.error("Error saving flow:", error)
  }
}

export const fetchWorkflow = async (
  session: Session,
  workflowId: string
): Promise<WorkflowMetadata> => {
  try {
    const client = getAuthenticatedClient(session)
    const response = await client.get<WorkflowMetadata>(
      `/workflows/${workflowId}`
    )
    console.log("Workflow fetched successfully", response.data)
    return workflowMetadataSchema.parse(response.data)
  } catch (error) {
    console.error("Error fetching workflow:", error)
    throw error
  }
}

export const createWorkflow = async (
  session: Session,
  title: string,
  description: string = ""
): Promise<WorkflowMetadata> => {
  const client = getAuthenticatedClient(session)
  const response = await client.post<WorkflowMetadata>(
    "/workflows",
    JSON.stringify({
      title,
      description,
    }),
    {
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        "Content-Type": "application/json",
      },
    }
  )
  console.log("Workflow created successfully", response.data)
  return workflowMetadataSchema.parse(response.data)
}

export const fetchAllWorkflows = async (
  session: Session
): Promise<WorkflowMetadata[]> => {
  try {
    const client = getAuthenticatedClient(session)
    const response = await client.get<WorkflowMetadata[]>("/workflows")
    let workflows = response.data

    console.log("Workflows fetched successfully", workflows)

    if (workflows.length === 0) {
      console.log("No workflows found. Creating a new one.")
      const newWorkflow = await createWorkflow(
        session,
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
