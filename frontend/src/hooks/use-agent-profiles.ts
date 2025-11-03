import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type AgentProfileCreate,
  type AgentProfileRead,
  type AgentProfileUpdate,
  agentProfilesCreateAgentProfile,
  agentProfilesDeleteAgentProfile,
  agentProfilesListAgentProfiles,
  agentProfilesUpdateAgentProfile,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"

export function useAgentProfiles(
  workspaceId?: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: profiles,
    isLoading: profilesIsLoading,
    error: profilesError,
    refetch: refetchProfiles,
  } = useQuery<AgentProfileRead[], TracecatApiError>({
    queryKey: ["agent-profiles", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("workspaceId is required to list agent profiles")
      }
      return await agentProfilesListAgentProfiles({ workspaceId })
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })

  return {
    profiles,
    profilesIsLoading,
    profilesError,
    refetchProfiles,
  }
}

export function useCreateAgentProfile(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createAgentProfile,
    isPending: createAgentProfileIsPending,
    error: createAgentProfileError,
  } = useMutation<AgentProfileRead, TracecatApiError, AgentProfileCreate>({
    mutationFn: async (payload) =>
      await agentProfilesCreateAgentProfile({
        workspaceId,
        requestBody: payload,
      }),
    onSuccess: (profile) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-profiles", workspaceId],
      })
      toast({
        title: "Agent profile created",
        description: `Saved ${profile.name}`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to create agent profile."
      toast({
        title: "Create failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    createAgentProfile,
    createAgentProfileIsPending,
    createAgentProfileError,
  }
}

export function useUpdateAgentProfile(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateAgentProfile,
    isPending: updateAgentProfileIsPending,
    error: updateAgentProfileError,
  } = useMutation<
    AgentProfileRead,
    TracecatApiError,
    AgentProfileUpdate & { profileId: string }
  >({
    mutationFn: async ({ profileId, ...requestBody }) =>
      await agentProfilesUpdateAgentProfile({
        workspaceId,
        profileId,
        requestBody,
      }),
    onSuccess: (profile) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-profiles", workspaceId],
      })
      toast({
        title: "Agent profile updated",
        description: `Saved ${profile.name}`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to update agent profile."
      toast({
        title: "Update failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    updateAgentProfile,
    updateAgentProfileIsPending,
    updateAgentProfileError,
  }
}

export function useDeleteAgentProfile(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteAgentProfile,
    isPending: deleteAgentProfileIsPending,
    error: deleteAgentProfileError,
  } = useMutation<
    void,
    TracecatApiError,
    { profileId: string; profileName?: string }
  >({
    mutationFn: async ({ profileId }) =>
      await agentProfilesDeleteAgentProfile({
        workspaceId,
        profileId,
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-profiles", workspaceId],
      })
      const label = variables.profileName ?? variables.profileId
      toast({
        title: "Agent profile deleted",
        description: `Removed ${label}`,
      })
    },
    onError: (error, variables) => {
      const label =
        variables?.profileName ?? variables?.profileId ?? "agent profile"
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to delete agent profile."
      toast({
        title: "Delete failed",
        description: `${label}: ${detail}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteAgentProfile,
    deleteAgentProfileIsPending,
    deleteAgentProfileError,
  }
}
