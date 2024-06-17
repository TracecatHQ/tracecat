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
import {
  MutateFunction,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { AxiosError } from "axios"

import { Workflow } from "@/types/schemas"
import { commitWorkflow, fetchWorkflow, updateWorkflow } from "@/lib/workflow"
import { toast } from "@/components/ui/use-toast"

type WorkflowContextType = {
  workflow: Workflow | null
  workflowId: string | null
  isLoading: boolean
  error: Error | null
  isOnline: boolean
  setIsOnline: (isOnline: boolean) => void
  commit: MutateFunction<unknown, Error, void, unknown>
  update: MutateFunction<unknown, Error, Record<string, unknown>, unknown>
}
type TracecatErrorMessage = {
  type?: string
  message: string
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

export function WorkflowProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { workflowId } = useParams<{ workflowId: string }>()

  // Queries
  const {
    data: workflow,
    isLoading,
    error,
  } = useQuery<Workflow, Error>({
    queryKey: ["workflow", workflowId],
    queryFn: async ({ queryKey }) => {
      const [, workflowId] = queryKey as [string, string?]
      if (!workflowId) {
        throw new Error("No workflow ID provided")
      }
      return await fetchWorkflow(workflowId)
    },
  })

  // Mutations
  const { mutateAsync: commit } = useMutation({
    mutationFn: async () => await commitWorkflow(workflowId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
      toast({
        title: "Commited changes to workflow",
        description: "New workflow deployment created successfully.",
      })
    },
    onError: (error: AxiosError) => {
      console.error("Failed to commit workflow:", error)
      toast({
        title: "Error commiting workflow",
        description:
          (error.response?.data as TracecatErrorMessage).message ||
          "Could not commit workflow. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: update } = useMutation({
    mutationFn: async (values: Record<string, unknown>) =>
      await updateWorkflow(workflowId, values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
    onError: (error: AxiosError) => {
      console.error("Failed to update workflow:", error)
      toast({
        title: "Error updating workflow",
        description:
          (error.response?.data as TracecatErrorMessage).message ||
          "Could not update workflow. Please try again.",
        variant: "destructive",
      })
    },
  })

  // Other state
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
        commit,
        update,
      }}
    >
      {children}
    </WorkflowContext.Provider>
  )
}

export const useWorkflow = () => {
  const context = useContext(WorkflowContext)
  if (context === undefined) {
    throw new Error("useWorkflow must be used within a WorkflowProvider")
  }
  return context
}
