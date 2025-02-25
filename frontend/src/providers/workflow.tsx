"use client"

import React, {
  createContext,
  ReactNode,
  SetStateAction,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"
import {
  ApiError,
  RegistryActionValidateResponse,
  WorkflowCommitResponse,
  WorkflowRead,
  workflowsCommitWorkflow,
  workflowsGetWorkflow,
  workflowsUpdateWorkflow,
  WorkflowUpdate,
} from "@/client"
import {
  MutateFunction,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { AlertTriangleIcon } from "lucide-react"

import { toast } from "@/components/ui/use-toast"

type WorkflowContextType = {
  workflow: WorkflowRead | null
  workspaceId: string
  workflowId: string | null
  isLoading: boolean
  error: Error | null
  isOnline: boolean
  setIsOnline: (isOnline: boolean) => void
  commitWorkflow: MutateFunction<
    WorkflowCommitResponse,
    ApiError,
    void,
    unknown
  >
  updateWorkflow: MutateFunction<void, ApiError, WorkflowUpdate, unknown>
  validationErrors: RegistryActionValidateResponse[] | null
  setValidationErrors: React.Dispatch<
    SetStateAction<RegistryActionValidateResponse[] | null>
  >
}
type TracecatErrorMessage = {
  type?: string
  message: string
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

export function WorkflowProvider({
  workflowId,
  workspaceId,
  children,
}: {
  workflowId: string
  workspaceId: string
  children: ReactNode
}) {
  const queryClient = useQueryClient()
  const [validationErrors, setValidationErrors] = useState<
    RegistryActionValidateResponse[] | null
  >(null)

  // Queries
  const {
    data: workflow,
    isLoading,
    error,
  } = useQuery<WorkflowRead | null, ApiError>({
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
          title: "Saved changes to workflow",
          description: "New workflow version saved successfully.",
        })
      } else {
        const description = (
          <div className="flex flex-col space-y-2">
            <div className="flex items-center space-x-2">
              <AlertTriangleIcon className="size-4 fill-red-500 stroke-white" />
              <p className="font-semibold">
                {response.message ||
                  "Could not save workflow due to validation errors."}
              </p>
            </div>
            <p>Please hover over the save button to view errors.</p>
          </div>
        )
        toast({
          title: "Workflow validation failed",
          description,
        })
      }
    },
    onError: (error: ApiError) => {
      console.warn("Failed to save workflow:", error)
      toast({
        title: "Error saving workflow",
        description:
          (error.body as TracecatErrorMessage).message ||
          "Could not save workflow. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: updateWorkflow } = useMutation({
    mutationFn: async (values: WorkflowUpdate) =>
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
        validationErrors,
        setValidationErrors,
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
