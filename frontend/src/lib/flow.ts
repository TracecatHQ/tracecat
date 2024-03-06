import axios from "axios"
import { ReactFlowInstance } from "reactflow"


export type WorkflowMetadata = {
  id: string | undefined
  title: string | undefined
  description: string | undefined
  status: string | undefined
}


export async function saveFlow(
  workflowId: string,
  reactFlowInstance: ReactFlowInstance
) {
  if (!workflowId || !reactFlowInstance) return

  try {
    const flowObject = reactFlowInstance.toObject()
    const updateFlowObjectParams = JSON.stringify({
      object: JSON.stringify(flowObject),
    })
    await axios.post(
      `http://localhost:8000/workflows/${workflowId}`,
      updateFlowObjectParams,
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )

    console.log("Flow saved successfully")
  } catch (error) {
    console.error("Error saving flow:", error)
  }
}


export const fetchWorkflow = async (workflowNameId: string): Promise<WorkflowMetadata> => {
  try {
    const response = await axios.get<WorkflowMetadata>(
      `http://localhost:8000/workflows/${workflowNameId}`
    )
    console.log("Workflow fetched successfully", response.data)
    return response.data

  } catch (error) {
    console.error("Error fetching workflow:", error)
    throw error
  }
}

export const createWorkflow = async (
  workflowName: string,
  workflowDescription: string = ""
): Promise<WorkflowMetadata> => {
  const data = { title: workflowName, description: workflowDescription }
  const response = await axios.post<WorkflowMetadata>(
    `http://localhost:8000/workflows`,
    JSON.stringify(data),
    {
      headers: {
        "Content-Type": "application/json",
      },
    }
  )
  console.log("Workflow created successfully", response.data)
  return response.data
}

export const fetchWorkflows = async (): Promise<WorkflowMetadata[]> => {
  try {
    const response = await axios.get<WorkflowMetadata[]>(
      "http://localhost:8000/workflows"
    )
    let workflows = response.data

    if (workflows.length === 0) {
      const newWorkflow = await createWorkflow(
        "My first workflow",
        "Welcome to Tracecat. This is your first workflow!"
      )
      workflows = [newWorkflow]
    }

    return workflows
  } catch (error) {
    console.error("Error fetching workflows:", error)
    throw error
  }
}
