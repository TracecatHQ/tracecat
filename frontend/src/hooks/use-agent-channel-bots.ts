import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type AgentChannelBotCreate,
  type AgentChannelBotRead,
  type AgentChannelBotUpdate,
  type AgentChannelRouteRead,
  type AgentChannelRouteSet,
  agentChannelBotsAddBotRoute,
  agentChannelBotsCreateBot,
  agentChannelBotsDeleteBotRoute,
  agentChannelBotsDisconnectBot,
  agentChannelBotsGetBotAppManifest,
  agentChannelBotsInstallBot,
  agentChannelBotsInstallPlatformBot,
  agentChannelBotsListBotChannels,
  agentChannelBotsListBotRoutableChannels,
  agentChannelBotsListBotRoutes,
  agentChannelBotsListBots,
  agentChannelBotsListPlatformApps,
  agentChannelBotsSetBotRoute,
  agentChannelBotsUpdateBot,
  type ChannelBotAppManifest,
  type ChannelBotAppOwner,
  type ChannelBotChannel,
  type ChannelBotInstall,
  type ChannelBotInstallResult,
  type ChannelBotPlatformApp,
  type ChannelBotPlatformInstall,
  type ChannelBotProvider,
  type CursorPaginatedResponse_ChannelBotChannel_,
  type InboxChannelThread,
} from "@/client"
import {
  CHANNEL_BOT_ERROR_PARAM,
  CHANNEL_BOT_INSTALL_PARAM,
  CHANNEL_BOT_MESSAGE_PARAM,
  CHANNEL_BOT_PROVIDER_NAMES,
  channelBotReturnUrl,
  isChannelListingNotSupported,
  platformInstallState,
} from "@/components/agents/external-channels/channel-bot-utils"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { toast } from "@/components/ui/use-toast"
import { useDebounce } from "@/hooks/use-debounce"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  type BotChannels,
  type SlackPlacement,
  slackPlacementsByPreset,
} from "@/lib/agent-slack-placements"
import {
  getApiErrorDetail,
  retryHandler,
  type TracecatApiError,
} from "@/lib/errors"
import {
  type MutateOptions,
  type UseQueryResult,
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@/lib/query"

export const CHANNEL_BOTS_QUERY_KEY = "agent-channel-bots"
const INSTALL_MESSAGE_LIMIT = 200
// Only the first 100 channels a bot is in are read; the accepted bound per bot.
const BOT_CHANNEL_LIMIT = 100
// The API's cap; a tenant's channels are paged in for the route picker.
const ROUTABLE_CHANNEL_PAGE_LIMIT = 200
// The API's cap; every route of an install is read for the Agents list.
const ROUTE_PAGE_LIMIT = 200
// Defensive bound on route pages followed for one install.
const MAX_ROUTE_PAGES = 50
// Channel listings call Slack per bot, so mounts and focus reuse them briefly.
const CHANNEL_LIST_STALE_TIME = 60_000
// The app name is typed live; the manifest refetches once it settles.
const APP_NAME_DEBOUNCE_MS = 300

/** What an OAuth return needs the Channels tab to show beside the cards. */
export type ChannelBotInstallFailure = "slack_workspace_claimed"

/**
 * Toasts the result of an OAuth install the browser just returned from, except
 * a failure the tab explains inline. The parameters are untrusted, so titles are
 * fixed and the message is capped.
 */
export function useChannelBotInstallResult(): ChannelBotInstallFailure | null {
  const [failure, setFailure] = useState<ChannelBotInstallFailure | null>(null)
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
    // Only a platform install sets this code, so the claimed workspace is Slack's.
    if (url.searchParams.get(CHANNEL_BOT_ERROR_PARAM) === "workspace_claimed") {
      setFailure("slack_workspace_claimed")
      return
    }
    toast({
      title: "Could not connect the Slack bot",
      description: message || "Please try again.",
      variant: "destructive",
    })
  }, [])
  return failure
}

/**
 * Lists an agent preset's custom-app bots, every provider in one call. Never
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
        appOwner: "workspace",
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

/** The providers whose app this deployment registers, installable in one click. */
export function useChannelBotPlatformApps(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const { hasEntitlement } = useEntitlements()
  const {
    data: platformApps,
    isLoading: platformAppsIsLoading,
    error: platformAppsError,
  } = useQuery<Array<ChannelBotPlatformApp>, TracecatApiError>({
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, "platform-apps"],
    queryFn: async () =>
      await agentChannelBotsListPlatformApps({ workspaceId }),
    enabled: enabled && hasEntitlement("agent_channels_preview"),
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  return { platformApps, platformAppsIsLoading, platformAppsError }
}

/** The manifest that creates the bot's own provider app named `appName`; null until it loads. */
export function useChannelBotAppManifest(
  bot: AgentChannelBotRead,
  appName: string
) {
  const [settledName] = useDebounce(appName, APP_NAME_DEBOUNCE_MS)
  const { data, error } = useQuery<ChannelBotAppManifest, TracecatApiError>({
    queryKey: [
      CHANNEL_BOTS_QUERY_KEY,
      bot.workspace_id,
      bot.id,
      "app-manifest",
      settledName,
    ],
    queryFn: async () =>
      await agentChannelBotsGetBotAppManifest({
        workspaceId: bot.workspace_id,
        botId: bot.id,
        appName: settledName,
      }),
    enabled: settledName !== "",
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  // Until the name settles, the manifest still carries the previous one.
  const settled = settledName === appName
  return {
    appManifest: settled ? (data ?? null) : null,
    appManifestError: settled ? error : null,
  }
}

/**
 * Whether the workspace can manage Tracecat for Slack: the preview entitlement
 * and a Slack app the deployment registers.
 */
export function useSlackPlatformAppAvailable(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const { platformApps, platformAppsIsLoading, platformAppsError } =
    useChannelBotPlatformApps(workspaceId, { enabled })
  return {
    slackPlatformAvailable:
      enabled &&
      hasEntitlement("agent_channels_preview") &&
      platformApps?.some((app) => app.provider === "slack") === true,
    slackPlatformIsLoading: entitlementsLoading || platformAppsIsLoading,
    slackPlatformError: platformAppsError,
  }
}

/** The workspace's bots for `provider` whose app `appOwner` owns, any agent. */
function useChannelBotsOwnedBy(
  workspaceId: string,
  provider: ChannelBotProvider,
  appOwner: ChannelBotAppOwner,
  enabled: boolean
) {
  return useQuery<Array<AgentChannelBotRead>, TracecatApiError>({
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, appOwner, provider],
    queryFn: async () => {
      // Only the first 100 bots are read; the accepted bound per workspace.
      const page = await agentChannelBotsListBots({
        workspaceId,
        provider,
        appOwner,
        limit: 100,
      })
      return page.items
    },
    enabled,
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
}

/** The workspace's installs of the platform app for `provider`, any agent. */
export function usePlatformChannelBots(
  workspaceId: string,
  provider: ChannelBotProvider,
  enabled: boolean
) {
  const {
    data: installs,
    isLoading: installsIsLoading,
    error: installsError,
  } = useChannelBotsOwnedBy(workspaceId, provider, "platform", enabled)
  return { installs, installsIsLoading, installsError }
}

/**
 * Which agent answers new threads in each channel of a platform install, a page
 * at a time in channel ID order.
 */
export function useChannelBotRoutes(
  workspaceId: string,
  botId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const query = useInfiniteQuery({
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, botId, "routes"],
    queryFn: async ({ pageParam }) =>
      await agentChannelBotsListBotRoutes({
        workspaceId,
        botId,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled,
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  const routes: AgentChannelRouteRead[] | undefined = query.data?.pages.flatMap(
    (page) => page.items
  )
  return {
    routes,
    routesIsLoading: query.isLoading,
    routesError: query.error as TracecatApiError | null,
    hasMoreRoutes: query.hasNextPage,
    loadingMoreRoutes: query.isFetchingNextPage,
    loadMoreRoutes: () => void query.fetchNextPage(),
  }
}

/** Every route of a platform install, following the cursor to the end. */
function useAllChannelBotRoutes(
  workspaceId: string,
  botId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const query = useQuery<AgentChannelRouteRead[], TracecatApiError>({
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, botId, "routes", "all"],
    queryFn: async () => {
      const routes: AgentChannelRouteRead[] = []
      let cursor: string | null = null
      for (let page = 0; page < MAX_ROUTE_PAGES; page++) {
        const result = await agentChannelBotsListBotRoutes({
          workspaceId,
          botId,
          limit: ROUTE_PAGE_LIMIT,
          cursor,
        })
        routes.push(...result.items)
        if (!result.has_more || !result.next_cursor) break
        cursor = result.next_cursor
      }
      return routes
    },
    enabled,
    staleTime: CHANNEL_LIST_STALE_TIME,
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  return { routes: query.data, routesError: query.error }
}

/** Whether any channel of a platform install routes to the agent, asked of the server. */
export function useChannelRoutesToAgent(
  workspaceId: string,
  botId: string,
  agentPresetId: string
) {
  const { data: routed } = useQuery<boolean, TracecatApiError>({
    queryKey: [
      CHANNEL_BOTS_QUERY_KEY,
      workspaceId,
      botId,
      "routes",
      agentPresetId,
    ],
    queryFn: async () => {
      const page = await agentChannelBotsListBotRoutes({
        workspaceId,
        botId,
        agentPresetId,
        limit: 1,
      })
      return page.items.length > 0
    },
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  return { routed: routed === true }
}

/** A provider that cannot list channels never will, so that refusal is not retried. */
function retryChannelListing(failureCount: number, error: Error): boolean {
  return (
    !isChannelListingNotSupported(error) &&
    retryHandler(failureCount, error as TracecatApiError)
  )
}

function channelBotChannelsQuery(workspaceId: string, botId: string) {
  return {
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, botId, "channels"],
    queryFn: async () =>
      await agentChannelBotsListBotChannels({
        workspaceId,
        botId,
        limit: BOT_CHANNEL_LIMIT,
      }),
    staleTime: CHANNEL_LIST_STALE_TIME,
    retry: retryChannelListing,
    meta: { suppressErrorToast: true },
  }
}

/** The first page of the channels a running bot is in, for an install of any app. */
export function useChannelBotChannels(
  workspaceId: string,
  botId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const query = useQuery({
    ...channelBotChannelsQuery(workspaceId, botId),
    enabled,
  })
  return {
    channels: query.data?.items,
    hasMoreChannels: query.data?.has_more === true,
    channelsIsLoading: query.isLoading,
    channelsError: query.error as TracecatApiError | null,
  }
}

/** Whether channel bot reads may run: the API needs the preview and `agent:read`. */
export function useChannelBotReadsAllowed(): boolean {
  const { hasEntitlement } = useEntitlements()
  const canReadAgents = useScopeCheck("agent:read") === true
  return hasEntitlement("agent_channels_preview") && canReadAgents
}

/**
 * The channel a session's linked thread is in, when its bot's cached first page
 * of channels names it. Never requests without the preview entitlement and
 * `agent:read`, which the API requires.
 */
export function useChannelThreadChannel(
  workspaceId: string,
  channelThread: InboxChannelThread
): ChannelBotChannel | undefined {
  const enabled = useChannelBotReadsAllowed()
  const botId = channelThread.bot_id
  const { channels } = useChannelBotChannels(workspaceId, botId, { enabled })
  // A skipped query still returns data cached before the gate closed.
  if (!enabled) return undefined
  return channels?.find(
    (channel) => channel.id === channelThread.provider_channel_id
  )
}

/** The channels a route can name, from the bot's provider tenant, a page at a time. */
export function useChannelBotRoutableChannels(
  workspaceId: string,
  botId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const query = useInfiniteQuery({
    queryKey: [CHANNEL_BOTS_QUERY_KEY, workspaceId, botId, "routable-channels"],
    queryFn: async ({ pageParam }) =>
      await agentChannelBotsListBotRoutableChannels({
        workspaceId,
        botId,
        limit: ROUTABLE_CHANNEL_PAGE_LIMIT,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled,
    retry: retryChannelListing,
    meta: { suppressErrorToast: true },
  })
  const channels: ChannelBotChannel[] | undefined = query.data?.pages.flatMap(
    (page) => page.items
  )
  return {
    channels,
    channelsIsLoading: query.isLoading,
    channelsError: query.error as TracecatApiError | null,
    hasMoreChannels: query.hasNextPage,
    loadingMoreChannels: query.isFetchingNextPage,
    loadMoreChannels: () => void query.fetchNextPage(),
  }
}

function combineBotChannels(
  results: UseQueryResult<CursorPaginatedResponse_ChannelBotChannel_>[]
): BotChannels[] {
  return results.map(({ data, error }) => ({ channels: data?.items, error }))
}

/**
 * Where each agent answers in Slack, by preset ID: the channels each running
 * bot is in, from the first page of each. A platform install's routed channels
 * go to their agents and the rest to its default agent. A bot whose channels
 * cannot be listed falls back to answering everywhere. Never requests without
 * the preview entitlement and `agent:read`.
 */
export function useAgentSlackPlacements(
  workspaceId: string
): Map<string, SlackPlacement[]> {
  const enabled = useChannelBotReadsAllowed()
  const { slackPlatformAvailable } = useSlackPlatformAppAvailable(workspaceId, {
    enabled,
  })
  const { installs } = usePlatformChannelBots(
    workspaceId,
    "slack",
    slackPlatformAvailable
  )
  const { data: workspaceBots } = useChannelBotsOwnedBy(
    workspaceId,
    "slack",
    "workspace",
    enabled
  )
  // A skipped query still returns data cached before the gate closed.
  const { install, state } = platformInstallState(
    slackPlatformAvailable ? installs : undefined
  )
  const activeInstall = state === "active" ? install : null
  const botId = activeInstall?.id ?? ""
  const { routes, routesError } = useAllChannelBotRoutes(workspaceId, botId, {
    enabled: activeInstall !== null,
  })

  const ownBots = useMemo(
    () =>
      (enabled ? (workspaceBots ?? []) : []).filter(
        (bot) => bot.status === "active"
      ),
    [enabled, workspaceBots]
  )
  const listedBots = useMemo(
    () => (activeInstall ? [activeInstall, ...ownBots] : ownBots),
    [activeInstall, ownBots]
  )
  const botChannels = useQueries({
    queries: listedBots.map((bot) =>
      channelBotChannelsQuery(workspaceId, bot.id)
    ),
    combine: useCallback(combineBotChannels, []),
  })

  return useMemo(
    () =>
      slackPlacementsByPreset({
        ownBots,
        install: activeInstall,
        channelsByBot: new Map(
          listedBots.map((bot, index) => [bot.id, botChannels[index]])
        ),
        routes: activeInstall ? routes : undefined,
        routesError,
      }),
    [activeInstall, botChannels, listedBots, ownBots, routes, routesError]
  )
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
  verb: string,
  conflict?: string
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
        description:
          (error.status === 409 ? conflict : undefined) ??
          getApiErrorDetail(error) ??
          "Please try again.",
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

/** Installs the platform app, or reconnects an install of it. */
export function useInstallPlatformChannelBot(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: installPlatformBot, isPending: installPlatformBotIsPending } =
    useChannelBotMutation<ChannelBotInstallResult, ChannelBotPlatformInstall>(
      workspaceId,
      provider,
      async (requestBody) =>
        await agentChannelBotsInstallPlatformBot({ workspaceId, requestBody }),
      "connect"
    )
  return { installPlatformBot, installPlatformBotIsPending }
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

/** Routes a channel that has no route yet; the API refuses one that has. */
export function useAddChannelBotRoute(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: addRoute } = useChannelBotMutation(
    workspaceId,
    provider,
    async ({
      botId,
      requestBody,
    }: {
      botId: string
      requestBody: AgentChannelRouteSet
    }) =>
      await agentChannelBotsAddBotRoute({ workspaceId, botId, requestBody }),
    "route a channel for",
    "This channel is already routed. Change its agent in the list instead."
  )
  return { addRoute }
}

/** Changes the agent a routed channel's new threads go to. */
export function useSetChannelBotRoute(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: setRoute } = useChannelBotMutation(
    workspaceId,
    provider,
    async ({
      botId,
      requestBody,
    }: {
      botId: string
      requestBody: AgentChannelRouteSet
    }) =>
      await agentChannelBotsSetBotRoute({ workspaceId, botId, requestBody }),
    "route a channel for"
  )
  return { setRoute }
}

/** Sends a channel's new threads back to the install's default agent. */
export function useDeleteChannelBotRoute(
  workspaceId: string,
  provider: ChannelBotProvider
) {
  const { mutate: deleteRoute } = useChannelBotMutation(
    workspaceId,
    provider,
    async ({
      botId,
      providerChannelId,
    }: {
      botId: string
      providerChannelId: string
    }) =>
      await agentChannelBotsDeleteBotRoute({
        workspaceId,
        botId,
        providerChannelId,
      }),
    "remove a channel route from"
  )
  return { deleteRoute }
}
