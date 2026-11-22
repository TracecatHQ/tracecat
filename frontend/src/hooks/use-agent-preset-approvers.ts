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
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

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

/** Workspace members, groups, and roles that can be named as approvers. */
export function useAgentPresetApproverOptions(workspaceId: string) {
  return useQuery<AgentPresetApproverRead[], TracecatApiError>({
    queryKey: ["agent-preset-approver-options", workspaceId],
    queryFn: async () =>
      await agentPresetsListAgentPresetApproverOptions({ workspaceId }),
    enabled: Boolean(workspaceId),
    retry: retryHandler,
  })
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
