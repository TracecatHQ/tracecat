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
  ApiError,
  CommitWorkflowResponse,
  UpdateWorkflowParams,
  WorkflowResponse,
  workflowsCommitWorkflow,
  workflowsGetWorkflow,
  workflowsUpdateWorkflow,
} from "@/client"
import {
  MutateFunction,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { toast } from "@/components/ui/use-toast"

type WorkflowContextType = {
  workflow: WorkflowResponse | null
  workspaceId: string
  workflowId: string | null
  isLoading: boolean
  error: Error | null
  isOnline: boolean
  setIsOnline: (isOnline: boolean) => void
  commitWorkflow: MutateFunction<
    CommitWorkflowResponse,
    ApiError,
    void,
    unknown
  >
  updateWorkflow: MutateFunction<void, ApiError, UpdateWorkflowParams, unknown>
}
type TracecatErrorMessage = {
  type?: string
  message: string
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

export function WorkflowProvider({
  workspaceId,
  children,
}: {
  workspaceId: string
  children: ReactNode
}) {
  const queryClient = useQueryClient()
  const { workflowId } = useParams<{ workflowId: string }>()

  // Queries
  const {
    data: workflow,
    isLoading,
    error,
  } = useQuery<WorkflowResponse | null, ApiError>({
    queryKey: ["workflow", workflowId],
    queryFn: async ({ queryKey }) => {
      const wfId = queryKey[1] as string | null
      if (!wfId) {
        return null
      }
      const workflow = await workflowsGetWorkflow({
        workspaceId,
        workflowId: queryKey[1] as string,
      })
      if (!workflow) {
        return null
      }
      return workflow
    },
  })

  // Mutations
  const { mutateAsync: commitWorkflow } = useMutation({
    mutationFn: async () =>
      await workflowsCommitWorkflow({ workspaceId, workflowId }),
    onSuccess: (response) => {
      if (response.status === "success") {
        queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
        toast({
          title: "Commited changes to workflow",
          description: "New workflow deployment created successfully.",
        })
      } else {
        toast({
          title: "Error commiting workflow",
          description: response.message || "Could not commit workflow.",
          variant: "destructive",
        })
      }
    },
    onError: (error: ApiError) => {
      console.warn("Failed to commit workflow:", error)
      toast({
        title: "Error commiting workflow",
        description:
          (error.body as TracecatErrorMessage).message ||
          "Could not commit workflow. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: updateWorkflow } = useMutation({
    mutationFn: async (values: UpdateWorkflowParams) =>
      await workflowsUpdateWorkflow({
        workspaceId,
        workflowId,
        requestBody: values,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
    onError: (error: ApiError) => {
      console.error("Failed to update workflow:", error)
      toast({
        title: "Error updating workflow",
        description:
          (error.body as TracecatErrorMessage).message ||
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
    async (isOnline: boolean) => {
      await workflowsUpdateWorkflow({
        workspaceId,
        workflowId,
        requestBody: {
          status: isOnline ? "online" : "offline",
        },
      }),
        setIsOnlineVisual(isOnline)
    },
    [workflowId]
  )
  return (
    <WorkflowContext.Provider
      value={{
        workspaceId,
        workflow: workflow || null,
        workflowId,
        isLoading,
        error,
        isOnline: isOnlineVisual,
        setIsOnline,
        commitWorkflow,
        updateWorkflow,
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
