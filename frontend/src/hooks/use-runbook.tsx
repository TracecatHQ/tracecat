import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type RunbookCreate,
  type RunbookRead,
  type RunbookRunRequest,
  type RunbookRunResponse,
  type RunbookUpdate,
  runbookCreateRunbook,
  runbookDeleteRunbook,
  runbookGetRunbook,
  runbookListRunbooks,
  runbookRunRunbook,
  runbookUpdateRunbook,
} from "@/client"
import { toast } from "@/components/ui/use-toast"

// Hook for creating a new runbook from a chat
export function useCreateRunbook(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: createRunbook, isPending: createRunbookPending } =
    useMutation<RunbookRead, ApiError, RunbookCreate>({
      mutationFn: (request: RunbookCreate) => {
        // Kick off the runbook creation
        const result = runbookCreateRunbook({
          requestBody: request,
          workspaceId,
        })
        // Concurrently toast to show accepted
        toast({
          title: "Creating runbook",
          description:
            "Processing your chat into a reusable runbook. This may take up to 30 seconds.",
        })
        return result
      },
      onSuccess: () => {
        // Invalidate and refetch runbook lists
        queryClient.invalidateQueries({ queryKey: ["runbooks", workspaceId] })
        toast({
          title: "Runbook created successfully",
          description:
            "The runbook has been created and is available for replay.",
        })
      },
    })

  return { createRunbook, createRunbookPending }
}

// Hook for listing runbooks
export function useListRunbooks({
  workspaceId,
  limit = 50,
  sortBy = "created_at",
  order = "desc",
}: {
  workspaceId: string
  limit?: number
  sortBy?: "created_at" | "updated_at"
  order?: "asc" | "desc"
}) {
  return useQuery<RunbookRead[], ApiError>({
    queryKey: ["runbooks", workspaceId, limit, sortBy, order],
    queryFn: () => runbookListRunbooks({ workspaceId, limit, sortBy, order }),
  })
}

// Hook for getting a single runbook
export function useGetRunbook({
  workspaceId,
  runbookId,
}: {
  workspaceId: string
  runbookId: string
}) {
  return useQuery<RunbookRead, ApiError>({
    queryKey: ["runbooks", workspaceId, runbookId],
    queryFn: () => runbookGetRunbook({ workspaceId, runbookId }),
    enabled: !!runbookId && !!workspaceId,
  })
}

// Hook for updating a runbook
export function useUpdateRunbook(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: updateRunbook, isPending: updateRunbookPending } =
    useMutation<
      RunbookRead,
      ApiError,
      { runbookId: string; request: RunbookUpdate }
    >({
      mutationFn: ({ runbookId, request }) =>
        runbookUpdateRunbook({
          runbookId,
          workspaceId,
          requestBody: request,
        }),
      onSuccess: (data) => {
        // Invalidate and refetch runbook lists and specific runbook
        queryClient.invalidateQueries({ queryKey: ["runbooks", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["runbooks", workspaceId, data.id],
        })
      },
    })

  return { updateRunbook, updateRunbookPending }
}

// Hook for deleting a runbook
export function useDeleteRunbook(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: deleteRunbook, isPending: deleteRunbookPending } =
    useMutation<void, ApiError, string>({
      mutationFn: (runbookId: string) =>
        runbookDeleteRunbook({ runbookId, workspaceId }),
      onSuccess: () => {
        // Invalidate and refetch runbook lists
        queryClient.invalidateQueries({ queryKey: ["runbooks", workspaceId] })
      },
    })

  return { deleteRunbook, deleteRunbookPending }
}

// Hook for running a runbook on cases
export function useRunRunbook(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: runRunbook, isPending: runRunbookPending } = useMutation<
    RunbookRunResponse,
    ApiError,
    { runbookId: string; request: RunbookRunRequest }
  >({
    mutationFn: ({ runbookId, request }) =>
      runbookRunRunbook({
        runbookId,
        workspaceId,
        requestBody: request,
      }),
    onSuccess: (data, variables) => {
      // Invalidate chats to force refresh when a runbook is executed
      // This ensures the new chat appears in the chat list
      if (variables.request.entities && variables.request.entities.length > 0) {
        // Invalidate cache for all entities, not just the first one
        for (const entity of variables.request.entities) {
          queryClient.invalidateQueries({
            queryKey: [
              "chats",
              workspaceId,
              entity.entity_type,
              entity.entity_id,
            ],
          })
        }
      }

      toast({
        title: "Runbook executed successfully",
      })
    },
  })

  return { runRunbook, runRunbookPending }
}
