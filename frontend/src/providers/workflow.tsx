"use client"

import {
  type MutateFunction,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import type React from "react"
import {
  createContext,
  type ReactNode,
  type SetStateAction,
  useContext,
  useState,
} from "react"
import {
  type ApiError,
  type ValidationResult,
  type WorkflowCommitResponse,
  type WorkflowDslPublish,
  type WorkflowDslPublishResult,
  type WorkflowRead,
  type WorkflowUpdate,
  workflowsCommitWorkflow,
  workflowsGetWorkflow,
  workflowsPublishWorkflow,
  workflowsUpdateWorkflow,
} from "@/client"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"

type WorkflowContextType = {
  workflow: WorkflowRead | null
  workspaceId: string
  workflowId: string | null
  isLoading: boolean
  error: Error | null
  commitWorkflow: MutateFunction<
    WorkflowCommitResponse,
    ApiError,
    void,
    unknown
  >
  publishWorkflow: MutateFunction<
    WorkflowDslPublishResult,
    ApiError,
    WorkflowDslPublish,
    unknown
  >
  updateWorkflow: MutateFunction<void, ApiError, WorkflowUpdate, unknown>
  validationErrors: ValidationResult[] | null
  setValidationErrors: React.Dispatch<SetStateAction<ValidationResult[] | null>>
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
    ValidationResult[] | null
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
        const errorCount = response.errors?.length ?? 1
        toast({
          title: `Workflow validation failed with ${errorCount} ${errorCount === 1 ? "error" : "errors"}`,
          description: "Please hover over the save button to view errors.",
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

  const { mutateAsync: publishWorkflow } = useMutation({
    mutationFn: async (params: WorkflowDslPublish) =>
      await workflowsPublishWorkflow({
        workspaceId,
        workflowId,
        requestBody: params,
      }),
    onSuccess: (response) => {
      let title = "Workflow committed"
      if (response.status === "no_op") {
        if (response.pr_url && response.pr_reused) {
          title = "No changes (PR reused)"
        } else if (response.pr_url) {
          title = "No changes (PR created)"
        } else {
          title = "No changes to publish"
        }
      } else if (response.pr_url && response.pr_reused) {
        title = "Workflow committed (PR reused)"
      } else if (response.pr_url) {
        title = "Workflow committed (PR created)"
      }

      toast({
        title,
        description: response.message,
        action: response.pr_url ? (
          <ToastAction
            altText="Open pull request"
            onClick={() =>
              window.open(
                response.pr_url ?? "",
                "_blank",
                "noopener,noreferrer"
              )
            }
          >
            View PR
          </ToastAction>
        ) : undefined,
      })
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
    onError: (error: ApiError) => {
      console.warn("Failed to publish workflow:", error)
      const apiError = error as TracecatApiError<string>
      const detail = apiError.body?.detail
      switch (apiError.status) {
        case 400:
          toast({
            title: "Failed to publish workflow",
            description:
              detail ||
              "Invalid workflow configuration. Please check the logs for more details.",
          })
          break
        case 403:
          toast({
            title: "Failed to publish workflow",
            description:
              detail || "You don't have permission to publish this workflow.",
          })
          break
        case 404:
          toast({
            title: "Workflow definition not found",
            description:
              detail || "Please save the workflow before publishing.",
          })
          break
        case 409:
          toast({
            title: "Failed to publish workflow",
            description:
              detail ||
              "There was a conflict publishing the workflow. Please try again.",
          })
          break
        case 422:
          toast({
            title: "Failed to publish workflow",
            description:
              detail ||
              "Workflow validation failed. Please check your configuration.",
          })
          break
        default:
          toast({
            title: "Failed to publish workflow",
            description:
              detail || "Could not publish workflow. Please try again.",
            variant: "destructive",
          })
      }
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
    onError: (error: TracecatApiError) => {
      console.error("Failed to update workflow:", error)
      switch (error.status) {
        case 409:
          toast({
            title: "Failed to update workflow",
            description:
              String(error.body.detail) ||
              "There was a conflict updating the workflow. Please try again.",
          })
          break
        default:
          toast({
            title: "Failed to update workflow",
            description:
              String(error.body.detail) ||
              "Could not update workflow. Please the logs for more details.",
          })
      }
    },
  })

  return (
    <WorkflowContext.Provider
      value={{
        workspaceId,
        workflow: workflow || null,
        workflowId,
        isLoading,
        error,
        commitWorkflow,
        publishWorkflow,
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
