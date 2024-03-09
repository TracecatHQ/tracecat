"use client"

import React, {
  createContext,
  ReactNode,
  useContext,
  useEffect,
  useState,
} from "react"
import axios from "axios"

import { WorkflowMetadata } from "@/types/schemas"

type MaybeWorkflowMetadata = WorkflowMetadata | null
type WorkflowContextType = {
  workflowMetadata: MaybeWorkflowMetadata
  setWorkflowMetadata: (workflow: MaybeWorkflowMetadata) => void
  workflowId: string | null
  setWorkflowId: (id: string) => void
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

export const WorkflowProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const [workflowMetadata, setWorkflowMetadata] =
    useState<MaybeWorkflowMetadata>(null)
  const [workflowId, setWorkflowId] = useState<string | null>(null)
  useEffect(() => {
    async function fetchWorkflowId(id: string) {
      const response = await axios.get<WorkflowMetadata>(
        `http://localhost:8000/workflows/${id}`
      )
      setWorkflowMetadata(response.data)
    }
    if (workflowId) {
      console.log("fetching workflow id", workflowId)
      fetchWorkflowId(workflowId)
    }
  }, [workflowId])
  return (
    <WorkflowContext.Provider
      value={{
        workflowMetadata,
        setWorkflowMetadata,
        workflowId,
        setWorkflowId,
      }}
    >
      {children}
    </WorkflowContext.Provider>
  )
}

export const useWorkflowMetadata = () => {
  const context = useContext(WorkflowContext)
  if (context === undefined) {
    throw new Error("useWorkflow must be used within a WorkflowProvider")
  }
  return context
}
