import {
  type AgentPresetApproverRead,
  type AgentPresetApproverRef,
  agentPresetsAddAgentPresetApprovers,
  agentPresetsListAgentPresetApproverOptions,
  agentPresetsListAgentPresetApprovers,
  agentPresetsRemoveAgentPresetApprovers,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getAgentPresetErrorMessage } from "@/lib/agent-presets"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useInfiniteQuery, useMutation, useQueryClient } from "@/lib/query"

/** Who may decide a preset's tool approvals, a page at a time; none = default rule. */
export function useAgentPresetApprovers(
  workspaceId: string,
  presetId: string | null | undefined
) {
  const query = useInfiniteQuery({
    queryKey: ["agent-preset-approvers", workspaceId, presetId],
    queryFn: async ({ pageParam }) => {
      if (!presetId) {
        throw new Error("presetId is required")
      }
      return await agentPresetsListAgentPresetApprovers({
        workspaceId,
        presetId,
        cursor: pageParam,
      })
    },
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled: Boolean(workspaceId) && Boolean(presetId),
    retry: retryHandler,
  })
  const approvers: AgentPresetApproverRead[] =
    query.data?.pages.flatMap((page) => page.items) ?? []
  return { ...query, approvers }
}

/** Principals of this workspace matching `search`, a page at a time. */
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

/** Add or remove a preset's approvers; changes apply at once, unversioned. */
export function useChangeAgentPresetApprovers(
  workspaceId: string,
  presetId: string | null
) {
  const queryClient = useQueryClient()
  return useMutation<
    unknown,
    TracecatApiError,
    { action: "add" | "remove"; approvers: AgentPresetApproverRef[] }
  >({
    mutationFn: async ({ action, approvers }) => {
      if (!presetId) {
        throw new Error("presetId is required")
      }
      const request = { workspaceId, presetId, requestBody: { approvers } }
      return action === "add"
        ? await agentPresetsAddAgentPresetApprovers(request)
        : await agentPresetsRemoveAgentPresetApprovers(request)
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["agent-preset-approvers", workspaceId, presetId],
      })
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
