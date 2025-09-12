import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type PromptCreate,
  type PromptRead,
  type PromptRunRequest,
  type PromptRunResponse,
  type PromptUpdate,
  promptCreatePrompt,
  promptDeletePrompt,
  promptGetPrompt,
  promptListPrompts,
  promptRunPrompt,
  promptUpdatePrompt,
} from "@/client"
import { toast } from "@/components/ui/use-toast"

// Hook for creating a new prompt from a chat
export function useCreatePrompt(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: createPrompt, isPending: createPromptPending } =
    useMutation<PromptRead, ApiError, PromptCreate>({
      mutationFn: (request: PromptCreate) => {
        // Kick off the prompt creation
        const result = promptCreatePrompt({
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
        // Invalidate and refetch prompt lists
        queryClient.invalidateQueries({ queryKey: ["prompts", workspaceId] })
        toast({
          title: "Runbook created successfully",
          description:
            "The runbook has been created and is available for replay.",
        })
      },
    })

  return { createPrompt, createPromptPending }
}

// Hook for listing prompts
export function useListPrompts({
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
  return useQuery<PromptRead[], ApiError>({
    queryKey: ["prompts", workspaceId, limit, sortBy, order],
    queryFn: () => promptListPrompts({ workspaceId, limit, sortBy, order }),
  })
}

// Hook for getting a single prompt
export function useGetPrompt({
  workspaceId,
  promptId,
}: {
  workspaceId: string
  promptId: string
}) {
  return useQuery<PromptRead, ApiError>({
    queryKey: ["prompts", workspaceId, promptId],
    queryFn: () => promptGetPrompt({ workspaceId, promptId }),
  })
}

// Hook for updating a prompt
export function useUpdatePrompt(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: updatePrompt, isPending: updatePromptPending } =
    useMutation<
      PromptRead,
      ApiError,
      { promptId: string; request: PromptUpdate }
    >({
      mutationFn: ({ promptId, request }) =>
        promptUpdatePrompt({
          promptId,
          workspaceId,
          requestBody: request,
        }),
      onSuccess: (data) => {
        // Invalidate and refetch prompt lists and specific prompt
        queryClient.invalidateQueries({ queryKey: ["prompts", workspaceId] })
        queryClient.invalidateQueries({
          queryKey: ["prompts", workspaceId, data.id],
        })
      },
    })

  return { updatePrompt, updatePromptPending }
}

// Hook for deleting a prompt
export function useDeletePrompt(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: deletePrompt, isPending: deletePromptPending } =
    useMutation<void, ApiError, string>({
      mutationFn: (promptId: string) =>
        promptDeletePrompt({ promptId, workspaceId }),
      onSuccess: () => {
        // Invalidate and refetch prompt lists
        queryClient.invalidateQueries({ queryKey: ["prompts", workspaceId] })
      },
    })

  return { deletePrompt, deletePromptPending }
}

// Hook for running a prompt on cases
export function useRunPrompt(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: runPrompt, isPending: runPromptPending } = useMutation<
    PromptRunResponse,
    ApiError,
    { promptId: string; request: PromptRunRequest }
  >({
    mutationFn: ({ promptId, request }) =>
      promptRunPrompt({
        promptId,
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

  return { runPrompt, runPromptPending }
}
