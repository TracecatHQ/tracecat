"use client"

import React, {
  createContext,
  ReactNode,
  useContext,
  useEffect,
  useState,
} from "react"
import { Session } from "@supabase/supabase-js"

import { WorkflowMetadata } from "@/types/schemas"
import { fetchWorkflow } from "@/lib/flow"

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

interface WorkflowProviderProps {
  session: Session | null
  children: ReactNode
}
export const WorkflowProvider: React.FC<WorkflowProviderProps> = ({
  session,
  children,
}) => {
  const [workflowMetadata, setWorkflowMetadata] =
    useState<MaybeWorkflowMetadata>(null)
  const [workflowId, setWorkflowId] = useState<string | null>(null)
  useEffect(() => {
    async function fetchWorkflowId(id: string) {
      const workflowMetadata = await fetchWorkflow(session, id)
      setWorkflowMetadata(workflowMetadata)
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
