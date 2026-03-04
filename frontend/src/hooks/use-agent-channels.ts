import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type AgentChannelTokenCreate,
  type AgentChannelTokenRead,
  type AgentChannelTokenUpdate,
  agentChannelsCreateChannelToken,
  agentChannelsDeleteChannelToken,
  agentChannelsListChannelTokens,
  agentChannelsRotateChannelToken,
  agentChannelsStartSlackOauth,
  agentChannelsUpdateChannelToken,
  type ChannelType,
  type SlackOAuthStartRequest as ClientSlackOAuthStartRequest,
  type SlackOAuthStartResponse,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"

type ListChannelTokenParams = {
  agentPresetId?: string
  channelType?: ChannelType
  enabled?: boolean
}

export function useAgentChannelTokens(
  workspaceId?: string,
  { agentPresetId, channelType, enabled = true }: ListChannelTokenParams = {}
) {
  const {
    data: tokens,
    isLoading: tokensIsLoading,
    error: tokensError,
    refetch: refetchTokens,
  } = useQuery<Array<AgentChannelTokenRead>, TracecatApiError>({
    queryKey: ["agent-channel-tokens", workspaceId, agentPresetId, channelType],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("workspaceId is required to list channel tokens")
      }
      return await agentChannelsListChannelTokens({
        workspaceId,
        agentPresetId,
        channelType,
      })
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })

  return {
    tokens,
    tokensIsLoading,
    tokensError,
    refetchTokens,
  }
}

export function useCreateAgentChannelToken(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createChannelToken,
    isPending: createChannelTokenIsPending,
    error: createChannelTokenError,
  } = useMutation<
    AgentChannelTokenRead,
    TracecatApiError,
    AgentChannelTokenCreate
  >({
    mutationFn: async (requestBody) =>
      await agentChannelsCreateChannelToken({
        workspaceId,
        requestBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-channel-tokens", workspaceId],
      })
      toast({
        title: "Channel token created",
        description: "Slack endpoint is ready.",
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to create channel token."
      toast({
        title: "Create failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    createChannelToken,
    createChannelTokenIsPending,
    createChannelTokenError,
  }
}

export function useUpdateAgentChannelToken(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateChannelToken,
    isPending: updateChannelTokenIsPending,
    error: updateChannelTokenError,
  } = useMutation<
    AgentChannelTokenRead,
    TracecatApiError,
    { tokenId: string; requestBody: AgentChannelTokenUpdate }
  >({
    mutationFn: async ({ tokenId, requestBody }) =>
      await agentChannelsUpdateChannelToken({
        workspaceId,
        tokenId,
        requestBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-channel-tokens", workspaceId],
      })
      toast({
        title: "Channel token updated",
        description: "Slack channel settings saved.",
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to update channel token."
      toast({
        title: "Update failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    updateChannelToken,
    updateChannelTokenIsPending,
    updateChannelTokenError,
  }
}

export function useRotateAgentChannelToken(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: rotateChannelToken,
    isPending: rotateChannelTokenIsPending,
    error: rotateChannelTokenError,
  } = useMutation<AgentChannelTokenRead, TracecatApiError, { tokenId: string }>(
    {
      mutationFn: async ({ tokenId }) =>
        await agentChannelsRotateChannelToken({
          workspaceId,
          tokenId,
        }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["agent-channel-tokens", workspaceId],
        })
        toast({
          title: "Endpoint rotated",
          description: "Use the new endpoint URL in Slack configuration.",
        })
      },
      onError: (error) => {
        const detail =
          typeof error.body?.detail === "string"
            ? error.body.detail
            : "Failed to rotate channel token."
        toast({
          title: "Rotate failed",
          description: detail,
          variant: "destructive",
        })
      },
    }
  )

  return {
    rotateChannelToken,
    rotateChannelTokenIsPending,
    rotateChannelTokenError,
  }
}

export function useDeleteAgentChannelToken(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteChannelToken,
    isPending: deleteChannelTokenIsPending,
    error: deleteChannelTokenError,
  } = useMutation<void, TracecatApiError, { tokenId: string }>({
    mutationFn: async ({ tokenId }) =>
      await agentChannelsDeleteChannelToken({
        workspaceId,
        tokenId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-channel-tokens", workspaceId],
      })
      toast({
        title: "Channel token deleted",
        description: "Slack endpoint has been revoked.",
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to delete channel token."
      toast({
        title: "Delete failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    deleteChannelToken,
    deleteChannelTokenIsPending,
    deleteChannelTokenError,
  }
}

type SlackOAuthStartArgs = {
  tokenId?: string
  agentPresetId: string
  clientId: string
  clientSecret: string
  signingSecret: string
  returnUrl: string
}

export function useStartSlackOAuth(workspaceId: string) {
  const {
    mutateAsync: startSlackOAuth,
    isPending: startSlackOAuthIsPending,
    error: startSlackOAuthError,
  } = useMutation<
    SlackOAuthStartResponse,
    TracecatApiError,
    SlackOAuthStartArgs
  >({
    mutationFn: async (params) => {
      const requestBody: ClientSlackOAuthStartRequest = {
        agent_preset_id: params.agentPresetId,
        client_id: params.clientId.trim(),
        client_secret: params.clientSecret.trim(),
        signing_secret: params.signingSecret.trim(),
        return_url: params.returnUrl,
      }
      if (params.tokenId) {
        requestBody.token_id = params.tokenId
      }
      return await agentChannelsStartSlackOauth({
        workspaceId,
        requestBody,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to start Slack connect flow."
      toast({
        title: "Slack connect failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    startSlackOAuth,
    startSlackOAuthIsPending,
    startSlackOAuthError,
  }
}
