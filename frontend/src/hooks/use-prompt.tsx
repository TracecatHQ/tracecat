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
          title: "Creating agenda",
          description:
            "Processing your chat into a reusable agenda. This may take up to 30 seconds.",
        })
        return result
      },
      onSuccess: () => {
        // Invalidate and refetch prompt lists
        queryClient.invalidateQueries({ queryKey: ["prompts", workspaceId] })
        toast({
          title: "Agenda created successfully",
          description:
            "The agenda has been created and is available for replay.",
        })
      },
    })

  return { createPrompt, createPromptPending }
}

// Hook for listing prompts
export function useListPrompts({
  workspaceId,
  limit = 50,
}: {
  workspaceId: string
  limit?: number
}) {
  return useQuery<PromptRead[], ApiError>({
    queryKey: ["prompts", workspaceId, limit],
    queryFn: () => promptListPrompts({ workspaceId, limit }),
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
        toast({
          title: "Prompt updated successfully",
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
        toast({
          title: "Prompt deleted successfully",
        })
      },
    })

  return { deletePrompt, deletePromptPending }
}

// Hook for running a prompt on cases
export function useRunPrompt(workspaceId: string) {
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
    onSuccess: () => {
      toast({
        title: "Prompt executed successfully",
      })
    },
  })

  return { runPrompt, runPromptPending }
}
