"use client"

import {
  type AgentOtelSettingsRead,
  type SettingsUpdateAgentOtelSettingsData,
  settingsGetAgentOtelSettings,
  settingsUpdateAgentOtelSettings,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

/** Load and update organization-scoped Agent OTel settings. */
export function useOrgAgentOtelSettings() {
  const queryClient = useQueryClient()
  const {
    data: agentOtelSettings,
    isLoading: agentOtelSettingsIsLoading,
    error: agentOtelSettingsError,
  } = useQuery<AgentOtelSettingsRead>({
    queryKey: ["org-agent-otel-settings"],
    queryFn: async () => await settingsGetAgentOtelSettings(),
  })

  const {
    mutateAsync: updateAgentOtelSettings,
    isPending: updateAgentOtelSettingsIsPending,
    error: updateAgentOtelSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateAgentOtelSettingsData) =>
      await settingsUpdateAgentOtelSettings(params),
    onSuccess: async () => {
      // Await the refetch so isPending spans it; the settings form stays
      // disabled until the seeding effect has re-run with fresh data.
      await queryClient.invalidateQueries({
        queryKey: ["org-agent-otel-settings"],
      })
      toast({
        title: "Updated agent telemetry",
        description: "Agent OTel settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to update agent OTel settings", {
            status: error.status,
            detail: error.body?.detail,
          })
          toast({
            title: "Failed to update agent telemetry",
            description: `An error occurred while updating agent OTel settings: ${error.body.detail}`,
          })
      }
    },
  })

  /** Latest cached settings; current after `updateAgentOtelSettings` resolves. */
  function getLatestAgentOtelSettings(): AgentOtelSettingsRead | undefined {
    return queryClient.getQueryData<AgentOtelSettingsRead>([
      "org-agent-otel-settings",
    ])
  }

  return {
    agentOtelSettings,
    getLatestAgentOtelSettings,
    agentOtelSettingsIsLoading,
    agentOtelSettingsError,
    updateAgentOtelSettings,
    updateAgentOtelSettingsIsPending,
    updateAgentOtelSettingsError,
  }
}
