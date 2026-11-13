import { useEffect } from "react"
import {
  type AgentChannelBotCreate,
  type AgentChannelBotRead,
  type AgentChannelBotUpdate,
  agentChannelBotsCreateBot,
  agentChannelBotsDisconnectBot,
  agentChannelBotsInstallBot,
  agentChannelBotsListBots,
  agentChannelBotsUpdateBot,
  type ChannelBotInstall,
  type ChannelBotInstallResult,
  type ChannelBotProvider,
} from "@/client"
import {
  CHANNEL_BOT_INSTALL_PARAM,
  CHANNEL_BOT_MESSAGE_PARAM,
  CHANNEL_BOT_PROVIDER_NAMES,
  channelBotReturnUrl,
} from "@/components/agents/external-channels/channel-bot-utils"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  getApiErrorDetail,
  retryHandler,
  type TracecatApiError,
} from "@/lib/errors"
import {
  type MutateOptions,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const CHANNEL_BOTS_QUERY_KEY = "agent-channel-bots"
const INSTALL_MESSAGE_LIMIT = 200

/**
 * Toasts the result of an OAuth install the browser just returned from. The
 * parameters are untrusted, so titles are fixed and the message is capped.
 */
export function useChannelBotInstallResult(): void {
  useEffect(() => {
    const url = new URL(window.location.href)
    const result = url.searchParams.get(CHANNEL_BOT_INSTALL_PARAM)
    if (!result) {
      return
    }
    window.history.replaceState({}, "", channelBotReturnUrl())
    // The OAuth return names no provider, so these titles stay Slack's.
    if (result === "success") {
      toast({ title: "Slack bot connected" })
      return
    }
    const message = (url.searchParams.get(CHANNEL_BOT_MESSAGE_PARAM) ?? "")
      .trim()
      .slice(0, INSTALL_MESSAGE_LIMIT)
    toast({
      title: "Could not connect the Slack bot",
      description: message || "Please try again.",
      variant: "destructive",
    })
  }, [])
}

/**
 * Lists an agent preset's channel bots, every provider in one call. Never
 * requests without the preview entitlement, which the API requires.
 */
export function useAgentChannelBots(
  workspaceId: string,
  agentPresetId: string
) {
  const { hasEntitlement } = useEntitlements()
  const {
    data: bots,
    isLoading: botsIsLoading,
    error: botsError,
  } = useQuery<Array<AgentChannelBotRead>, TracecatApiError>({
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, agentPresetId],
    queryFn: async () => {
      // Only the first 100 bots are read; the accepted bound per preset.
      const page = await agentChannelBotsListBots({
        workspaceId,
        agentPresetId,
        limit: 100,
      })
      return page.items
    },
    enabled: hasEntitlement("agent_channels_preview"),
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })

  return { bots, botsIsLoading, botsError }
}

/** Whether any channel bot mutation in the workspace is still running. */
export function useChannelBotsBusy(workspaceId: string): boolean {
  return (
    useIsMutating({ mutationKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId] }) > 0
  )
}

function useChannelBotMutation<TData, TVariables>(
  workspaceId: string,
  provider: ChannelBotProvider,
  mutationFn: (variables: TVariables) => Promise<TData>,
  verb: string
) {
  const queryClient = useQueryClient()
  const {
    mutate: send,
    reset,
    isPending,
  } = useMutation<TData, TracecatApiError, TVariables>({
    mutationKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId],
    mutationFn,
    // A reset mutation leaves the cache at once, taking any secret with it.
    gcTime: 0,
    // Returned so callers resume only after the bot list has refetched.
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId],
      }),
    onError: (error) => {
      toast({
        title: `Could not ${verb} the ${CHANNEL_BOT_PROVIDER_NAMES[provider]} bot`,
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })

  // Resets after the caller's callbacks; a reset in the hook's onSettled
  // would run first and drop them.
  function mutate(
    variables: TVariables,
    options: MutateOptions<TData, TracecatApiError, TVariables> = {}
  ): void {
    send(variables, {
      ...options,
      onSettled: (...args) => {
        options.onSettled?.(...args)
        reset()
      },
    })
  }

  return { mutate, isPending }
}

/** Reserves a bot, and its endpoint, before the provider app exists. */
export function useCreateAgentChannelBot(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: createBot, isPending: createBotIsPending } =
    useChannelBotMutation(
      workspaceId,
      provider,
      async (requestBody: AgentChannelBotCreate) =>
        await agentChannelBotsCreateBot({ workspaceId, requestBody }),
      "set up"
    )
  return { createBot, createBotIsPending }
}

/** Installs a provider app into a new or existing bot. */
export function useInstallAgentChannelBot(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: installBot, isPending: installBotIsPending } =
    useChannelBotMutation<ChannelBotInstallResult, ChannelBotInstall>(
      workspaceId,
      provider,
      async (requestBody) =>
        await agentChannelBotsInstallBot({ workspaceId, requestBody }),
      "connect"
    )
  return { installBot, installBotIsPending }
}

/** Pauses, resumes or replaces the credentials of a bot. */
export function useUpdateAgentChannelBot(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: updateBot, isPending: updateBotIsPending } =
    useChannelBotMutation(
      workspaceId,
      provider,
      async ({
        botId,
        requestBody,
      }: {
        botId: string
        requestBody: AgentChannelBotUpdate
      }) =>
        await agentChannelBotsUpdateBot({ workspaceId, botId, requestBody }),
      "update"
    )
  return { updateBot, updateBotIsPending }
}

/** Disconnects a bot so it stops receiving events. */
export function useDisconnectAgentChannelBot(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: disconnectBot } = useChannelBotMutation(
    workspaceId,
    provider,
    async (botId: string) =>
      await agentChannelBotsDisconnectBot({ workspaceId, botId }),
    "disconnect"
  )
  return { disconnectBot }
}
