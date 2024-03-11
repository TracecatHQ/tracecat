"use client"

import React, {
  createContext,
  ReactNode,
  useContext,
  useEffect,
  useState,
} from "react"
import { useParams } from "next/navigation"
import { Session } from "@supabase/supabase-js"
import { useQuery } from "@tanstack/react-query"

import { WorkflowResponse } from "@/types/schemas"
import { fetchWorkflow } from "@/lib/flow"

type WorkflowContextType = {
  workflow: WorkflowResponse | null
  workflowId: string | null
  isLoading: boolean
  error: Error | null
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

interface WorkflowProviderProps {
  session: Session | null
  children: ReactNode
}
export function WorkflowProvider({ session, children }: WorkflowProviderProps) {
  const { id: workflowId } = useParams<{ id: string }>()

  const {
    data: workflow,
    isLoading,
    error,
  } = useQuery<WorkflowResponse, Error>({
    queryKey: ["workflow", workflowId],
    queryFn: async ({ queryKey }) => {
      const [_, workflowId] = queryKey as [string, string?]
      if (!workflowId) {
        throw new Error("No workflow ID provided")
      }
      const data = await fetchWorkflow(session, workflowId)
      return data
    },
  })
  return (
    <WorkflowContext.Provider
      value={{
        workflow: workflow || null,
        workflowId,
        isLoading,
        error,
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
