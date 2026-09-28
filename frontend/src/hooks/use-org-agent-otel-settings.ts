"use client"

import {
  type AgentOtelSettingsRead,
  type SettingsUpdateAgentOtelSettingsData,
  settingsGetAgentOtelSettings,
  settingsUpdateAgentOtelSettings,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

/** Load and update organization-scoped agent telemetry settings. */
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
        description: "Agent telemetry settings updated successfully.",
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
          console.error("Failed to update agent telemetry settings", {
            status: error.status,
            detail: getApiErrorDetail(error),
          })
          toast({
            title: "Failed to update agent telemetry",
            description: `An error occurred while updating agent telemetry settings: ${getApiErrorDetail(error) ?? "unknown error"}`,
          })
      }
    },
  })

  return {
    agentOtelSettings,
    agentOtelSettingsIsLoading,
    agentOtelSettingsError,
    updateAgentOtelSettings,
    updateAgentOtelSettingsIsPending,
    updateAgentOtelSettingsError,
  }
}
