import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type AgentPresetCreate,
  type AgentPresetRead,
  type AgentPresetReadMinimal,
  type AgentPresetUpdate,
  agentPresetsCreateAgentPreset,
  agentPresetsDeleteAgentPreset,
  agentPresetsGetAgentPreset,
  agentPresetsListAgentPresets,
  agentPresetsUpdateAgentPreset,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"

export function useAgentPresets(
  workspaceId?: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: presets,
    isLoading: presetsIsLoading,
    error: presetsError,
    refetch: refetchPresets,
  } = useQuery<AgentPresetReadMinimal[], TracecatApiError>({
    queryKey: ["agent-presets", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("workspaceId is required to list agent presets")
      }
      return await agentPresetsListAgentPresets({ workspaceId })
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })

  return {
    presets,
    presetsIsLoading,
    presetsError,
    refetchPresets,
  }
}

export function useAgentPreset(
  workspaceId: string,
  presetId?: string | null,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: preset,
    isLoading: presetIsLoading,
    error: presetError,
    refetch: refetchPreset,
  } = useQuery<AgentPresetRead, TracecatApiError>({
    queryKey: ["agent-preset", workspaceId, presetId],
    queryFn: async () => {
      if (!workspaceId || !presetId) {
        throw new Error("workspaceId and presetId are required")
      }
      return await agentPresetsGetAgentPreset({ workspaceId, presetId })
    },
    enabled: enabled && Boolean(workspaceId) && Boolean(presetId),
    retry: retryHandler,
  })

  return {
    preset,
    presetIsLoading,
    presetError,
    refetchPreset,
  }
}

export function useCreateAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createAgentPreset,
    isPending: createAgentPresetIsPending,
    error: createAgentPresetError,
  } = useMutation<AgentPresetRead, TracecatApiError, AgentPresetCreate>({
    mutationFn: async (payload) =>
      await agentPresetsCreateAgentPreset({
        workspaceId,
        requestBody: payload,
      }),
    onSuccess: (preset) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      toast({
        title: "Agent preset created",
        description: `Saved ${preset.name}`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to create agent preset."
      toast({
        title: "Create failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    createAgentPreset,
    createAgentPresetIsPending,
    createAgentPresetError,
  }
}

export function useUpdateAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateAgentPreset,
    isPending: updateAgentPresetIsPending,
    error: updateAgentPresetError,
  } = useMutation<
    AgentPresetRead,
    TracecatApiError,
    AgentPresetUpdate & { presetId: string }
  >({
    mutationFn: async ({ presetId, ...requestBody }) =>
      await agentPresetsUpdateAgentPreset({
        workspaceId,
        presetId,
        requestBody,
      }),
    onSuccess: (preset) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset", workspaceId, preset.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["workspace-agent-providers-status", workspaceId],
      })
      toast({
        title: "Agent preset updated",
        description: `Saved ${preset.name}`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to update agent preset."
      toast({
        title: "Update failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    updateAgentPreset,
    updateAgentPresetIsPending,
    updateAgentPresetError,
  }
}

export function useDeleteAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteAgentPreset,
    isPending: deleteAgentPresetIsPending,
    error: deleteAgentPresetError,
  } = useMutation<
    void,
    TracecatApiError,
    { presetId: string; presetName?: string }
  >({
    mutationFn: async ({ presetId }) =>
      await agentPresetsDeleteAgentPreset({
        workspaceId,
        presetId,
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      const label = variables.presetName ?? variables.presetId
      toast({
        title: "Agent preset deleted",
        description: `Removed ${label}`,
      })
    },
    onError: (error, variables) => {
      const label =
        variables?.presetName ?? variables?.presetId ?? "agent preset"
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to delete agent preset."
      toast({
        title: "Delete failed",
        description: `${label}: ${detail}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteAgentPreset,
    deleteAgentPresetIsPending,
    deleteAgentPresetError,
  }
}
