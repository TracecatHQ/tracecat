import {
  type AgentPresetChannelAccess,
  type AgentPresetRead,
  agentPresetsSetAgentPresetChannelAccess,
  type ChannelAccessPolicy,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getAgentPresetErrorMessage } from "@/lib/agent-presets"
import type { TracecatApiError } from "@/lib/errors"
import { useMutation, useQueryClient } from "@/lib/query"

/** Sets who may act on a preset's agent through channels; live, never versioned. */
export function useSetAgentPresetChannelAccess(
  workspaceId: string,
  presetId: string
) {
  const queryClient = useQueryClient()
  const presetKey = ["agent-preset", workspaceId, presetId]
  const { mutate, isPending } = useMutation<
    AgentPresetChannelAccess,
    TracecatApiError,
    ChannelAccessPolicy
  >({
    mutationFn: async (policy) =>
      await agentPresetsSetAgentPresetChannelAccess({
        workspaceId,
        presetId,
        requestBody: { channel_access_policy: policy },
      }),
    onSuccess: ({ channel_access_policy }) => {
      queryClient.setQueryData<AgentPresetRead>(presetKey, (preset) =>
        preset ? { ...preset, channel_access_policy } : preset
      )
    },
    onError: (error) =>
      toast({
        title: "Channel access not saved",
        description: getAgentPresetErrorMessage(
          error,
          "Failed to update channel access."
        ),
        variant: "destructive",
      }),
  })
  return { setPolicy: mutate, setPolicyIsPending: isPending }
}
