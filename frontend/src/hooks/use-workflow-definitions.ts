import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type WorkflowDefinitionRead,
  type WorkflowRead,
  workflowsListWorkflowDefinitions,
  workflowsRestoreWorkflowDefinition,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"

export function useWorkflowDefinitions(
  workspaceId: string,
  workflowId?: string | null,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: definitions,
    isLoading: definitionsIsLoading,
    error: definitionsError,
    refetch: refetchDefinitions,
  } = useQuery<WorkflowDefinitionRead[], TracecatApiError>({
    queryKey: ["workflow-definitions", workspaceId, workflowId],
    queryFn: async () => {
      if (!workspaceId || !workflowId) {
        throw new Error("workspaceId and workflowId are required")
      }
      const definitions = await workflowsListWorkflowDefinitions({
        workspaceId,
        workflowId,
      })
      return [...definitions].sort((a, b) => b.version - a.version)
    },
    enabled: enabled && Boolean(workspaceId) && Boolean(workflowId),
    retry: retryHandler,
  })

  return {
    definitions,
    definitionsIsLoading,
    definitionsError,
    refetchDefinitions,
  }
}

export function useRestoreWorkflowDefinition(
  workspaceId: string,
  workflowId?: string | null
) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: restoreWorkflowDefinition,
    isPending: restoreWorkflowDefinitionIsPending,
    error: restoreWorkflowDefinitionError,
  } = useMutation<WorkflowRead, TracecatApiError, { version: number }>({
    mutationFn: async ({ version }) => {
      if (!workspaceId || !workflowId) {
        throw new Error("workspaceId and workflowId are required")
      }
      return await workflowsRestoreWorkflowDefinition({
        workspaceId,
        workflowId,
        version,
      })
    },
    onSuccess: (workflow, variables) => {
      if (!workflowId) {
        return
      }
      queryClient.setQueryData(["workflow", workflowId], workflow)
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
      queryClient.invalidateQueries({
        queryKey: ["graph", workspaceId, workflowId],
      })
      queryClient.invalidateQueries({
        queryKey: ["workflow-definitions", workspaceId, workflowId],
      })
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
      toast({
        title: "Version restored",
        description: `${workflow.title} now points to v${variables.version}.`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to restore workflow version."
      toast({
        title: "Restore failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    restoreWorkflowDefinition,
    restoreWorkflowDefinitionIsPending,
    restoreWorkflowDefinitionError,
  }
}
