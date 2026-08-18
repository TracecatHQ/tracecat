"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type AgentOtelSettingsRead,
  type SettingsUpdateAgentOtelSettingsData,
  settingsGetAgentOtelSettings,
  settingsUpdateAgentOtelSettings,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"

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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-agent-otel-settings"] })
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
          console.error("Failed to update agent OTel settings", error)
          toast({
            title: "Failed to update agent telemetry",
            description: `An error occurred while updating agent OTel settings: ${error.body.detail}`,
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
