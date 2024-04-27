"use client"

import React, {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"

import { Workflow } from "@/types/schemas"
import { fetchWorkflow, updateWorkflow } from "@/lib/flow"

type WorkflowContextType = {
  workflow: Workflow | null
  workflowId: string | null
  isLoading: boolean
  error: Error | null
  isOnline: boolean
  setIsOnline: (isOnline: boolean) => void
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

interface WorkflowProviderProps {
  children: ReactNode
}
export function WorkflowProvider({ children }: WorkflowProviderProps) {
  const { workflowId } = useParams<{ workflowId: string }>()

  const {
    data: workflow,
    isLoading,
    error,
  } = useQuery<Workflow, Error>({
    queryKey: ["workflow", workflowId],
    queryFn: async ({ queryKey }) => {
      const [_, workflowId] = queryKey as [string, string?]
      if (!workflowId) {
        throw new Error("No workflow ID provided")
      }
      return await fetchWorkflow(workflowId)
    },
  })
  const [isOnlineVisual, setIsOnlineVisual] = useState<boolean>(
    workflow?.status === "online"
  )
  useEffect(() => {
    if (workflow?.status) {
      setIsOnlineVisual(workflow.status === "online")
    }
  }, [workflow?.status])

  const setIsOnline = useCallback(
    (isOnline: boolean) => {
      updateWorkflow(workflowId, {
        status: isOnline ? "online" : "offline",
      })
      setIsOnlineVisual(isOnline)
    },
    [workflowId]
  )
  return (
    <WorkflowContext.Provider
      value={{
        workflow: workflow || null,
        workflowId,
        isLoading,
        error,
        isOnline: isOnlineVisual,
        setIsOnline,
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
