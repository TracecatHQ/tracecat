import {
  type AgentPresetApproverRead,
  type AgentPresetApproverRef,
  agentPresetsListAgentPresetApproverOptions,
  agentPresetsListAgentPresetApprovers,
  agentPresetsReplaceAgentPresetApprovers,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getAgentPresetErrorMessage } from "@/lib/agent-presets"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

/** Who may decide a preset's tool approvals; empty means the default rule. */
export function useAgentPresetApprovers(
  workspaceId: string,
  presetId: string | null | undefined
) {
  return useQuery<AgentPresetApproverRead[], TracecatApiError>({
    queryKey: ["agent-preset-approvers", workspaceId, presetId],
    queryFn: async () => {
      if (!presetId) {
        throw new Error("presetId is required")
      }
      return await agentPresetsListAgentPresetApprovers({
        workspaceId,
        presetId,
      })
    },
    enabled: Boolean(workspaceId) && Boolean(presetId),
    retry: retryHandler,
  })
}

/** Workspace members, groups, and roles matching `search`, a page at a time. */
export function useAgentPresetApproverOptions(
  workspaceId: string,
  search: string
) {
  const term = search.trim()
  const query = useInfiniteQuery({
    queryKey: ["agent-preset-approver-options", workspaceId, term],
    queryFn: async ({ pageParam }) =>
      await agentPresetsListAgentPresetApproverOptions({
        workspaceId,
        search: term || undefined,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled: Boolean(workspaceId),
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  const options: AgentPresetApproverRead[] =
    query.data?.pages.flatMap((page) => page.items) ?? []
  return { ...query, options }
}

/** Replace a preset's approvers; the change applies at once, unversioned. */
export function useReplaceAgentPresetApprovers(
  workspaceId: string,
  presetId: string | null
) {
  const queryClient = useQueryClient()
  return useMutation<
    AgentPresetApproverRead[],
    TracecatApiError,
    AgentPresetApproverRef[]
  >({
    mutationFn: async (approvers) => {
      if (!presetId) {
        throw new Error("presetId is required")
      }
      return await agentPresetsReplaceAgentPresetApprovers({
        workspaceId,
        presetId,
        requestBody: { approvers },
      })
    },
    onSuccess: (approvers) => {
      queryClient.setQueryData(
        ["agent-preset-approvers", workspaceId, presetId],
        approvers
      )
    },
    onError: (error) => {
      toast({
        title: "Approvers not saved",
        description: getAgentPresetErrorMessage(
          error,
          "Failed to update approvers."
        ),
        variant: "destructive",
      })
    },
  })
}
