import {
  type AgentChannelBotRead,
  type AgentChannelRouteRead,
  type AgentPresetRead,
  type AgentPresetReadMinimal,
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
  agentPresetsListAgentPresets,
  agentPresetsSetAgentPresetChannelAccess,
  type ChannelBotAppManifest,
  type ChannelBotChannel,
  type ChannelBotDetails,
  type OrganizationGetOrganizationEntitlementsResponse,
  type OrganizationGetOrganizationResponse,
  organizationGetOrganization,
  organizationGetOrganizationEntitlements,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"

// Test files call `jest.mock("@/client")`; these are its channel bot calls.
export const api = {
  listBots: jest.mocked(agentChannelBotsListBots),
  createBot: jest.mocked(agentChannelBotsCreateBot),
  installBot: jest.mocked(agentChannelBotsInstallBot),
  updateBot: jest.mocked(agentChannelBotsUpdateBot),
  disconnectBot: jest.mocked(agentChannelBotsDisconnectBot),
  appManifest: jest.mocked(agentChannelBotsGetBotAppManifest),
  listPlatformApps: jest.mocked(agentChannelBotsListPlatformApps),
  installPlatformBot: jest.mocked(agentChannelBotsInstallPlatformBot),
  listRoutes: jest.mocked(agentChannelBotsListBotRoutes),
  addRoute: jest.mocked(agentChannelBotsAddBotRoute),
  listChannels: jest.mocked(agentChannelBotsListBotChannels),
  listRoutableChannels: jest.mocked(agentChannelBotsListBotRoutableChannels),
  setRoute: jest.mocked(agentChannelBotsSetBotRoute),
  deleteRoute: jest.mocked(agentChannelBotsDeleteBotRoute),
  listPresets: jest.mocked(agentPresetsListAgentPresets),
  setChannelAccess: jest.mocked(agentPresetsSetAgentPresetChannelAccess),
}

export const WORKSPACE_ID = "workspace-1"
export const PRESET = {
  id: "preset-1",
  name: "Triage",
  channel_access_policy: "linked_approvals",
} as AgentPresetRead
// The workspace's agents, as the agent pickers list them.
export const AGENTS = [
  { id: PRESET.id, name: PRESET.name },
  { id: "preset-2", name: "Phishing" },
] as AgentPresetReadMinimal[]
export const SLACK_ENDPOINT = "https://api.example.com/agent/channels/slack/abc"
export const SLACK_REDIRECT =
  "https://api.example.com/agent/channel-bots/slack/oauth/callback"

type SlackBotDetails = Extract<ChannelBotDetails, { provider: "slack" }>

/** An installed Slack bot on an HTTP deployment. */
export function slackBot(
  overrides: Partial<AgentChannelBotRead> = {},
  details: Partial<SlackBotDetails> = {}
): AgentChannelBotRead {
  return {
    id: "bot-1",
    workspace_id: WORKSPACE_ID,
    agent_preset_id: PRESET.id,
    provider: "slack",
    app_owner: "workspace",
    status: "active",
    provider_tenant_id: "T1",
    provider_app_id: "A1",
    endpoint_url: SLACK_ENDPOINT,
    created_at: "2026-11-12T00:00:00Z",
    updated_at: "2026-11-12T00:00:00Z",
    details: {
      provider: "slack",
      transport: "http",
      provider_enterprise_id: null,
      provider_bot_user_id: "UBOT",
      granted_scopes: ["chat:write"],
      missing_scopes: [],
      update_needed: false,
      manifest_version: 1,
      client_id: "123.456",
      has_bot_token: true,
      has_signing_secret: true,
      has_client_secret: true,
      oauth_redirect_url: SLACK_REDIRECT,
      ...details,
    },
    ...overrides,
  }
}

/** A Slack bot reserved but not yet installed. */
export function reservedSlackBot(
  overrides: Partial<AgentChannelBotRead> = {},
  details: Partial<SlackBotDetails> = {}
): AgentChannelBotRead {
  return slackBot(
    {
      status: "needs_reconnect",
      provider_tenant_id: null,
      provider_app_id: null,
      ...overrides,
    },
    {
      provider_bot_user_id: null,
      granted_scopes: [],
      client_id: null,
      has_bot_token: false,
      has_signing_secret: false,
      has_client_secret: false,
      ...details,
    }
  )
}

/** A Slack app manifest naming `appName`, as the API serves it. */
export function slackAppManifest(appName: string): ChannelBotAppManifest {
  const body = { display_information: { name: appName } }
  return {
    filename: "slack-app-manifest.json",
    media_type: "application/json",
    encoding: "text",
    content: JSON.stringify(body, null, 2),
    create_url: `https://api.slack.com/apps?new_app=1&manifest_json=${encodeURIComponent(JSON.stringify(body))}`,
  }
}

/** Serves an app manifest naming the app it was asked for. */
export function serveAppManifest(): void {
  api.appManifest.mockImplementation(
    ({ appName }) =>
      new CancelablePromise((resolve) => resolve(slackAppManifest(appName)))
  )
}

/** Serves `bots` from the list call, filtered as the API filters them. The
 * deployment registers no platform app unless a test serves one. */
export function serveBots(bots: AgentChannelBotRead[]): void {
  api.listBots.mockImplementation(
    ({ agentPresetId, provider, appOwner }) =>
      new CancelablePromise((resolve) =>
        resolve({
          items: bots.filter(
            (bot) =>
              (agentPresetId == null ||
                bot.agent_preset_id === agentPresetId) &&
              (provider == null || bot.provider === provider) &&
              (appOwner == null || bot.app_owner === appOwner)
          ),
          next_cursor: null,
        })
      )
  )
  api.listPlatformApps.mockResolvedValue([])
  api.listRoutes.mockResolvedValue(routePage([]))
  api.listChannels.mockResolvedValue(channelPage([]))
  api.listRoutableChannels.mockResolvedValue(channelPage([]))
  api.listPresets.mockResolvedValue(AGENTS)
}

/** Serves `routes` as one page, filtered by install and agent as the API does. */
export function serveRoutes(routes: AgentChannelRouteRead[]): void {
  api.listRoutes.mockImplementation(
    ({ botId, agentPresetId }) =>
      new CancelablePromise((resolve) =>
        resolve(
          routePage(
            routes.filter(
              (r) =>
                r.bot_id === botId &&
                (agentPresetId == null || r.agent_preset_id === agentPresetId)
            )
          )
        )
      )
  )
}

/** One page of an install's routes; `nextCursor` means more follow. */
export function routePage(
  items: AgentChannelRouteRead[],
  nextCursor: string | null = null
) {
  return { items, next_cursor: nextCursor, has_more: nextCursor !== null }
}

/** One page of channels; `nextCursor` means more follow. */
export function channelPage(
  items: ChannelBotChannel[],
  nextCursor: string | null = null
) {
  return { items, next_cursor: nextCursor, has_more: nextCursor !== null }
}

/** An install of the platform app for Slack, by default for `PRESET`. */
export function platformSlackBot(
  overrides: Partial<AgentChannelBotRead> = {}
): AgentChannelBotRead {
  return slackBot({
    id: "platform-1",
    app_owner: "platform",
    provider_app_id: "APLAT",
    endpoint_url: null,
    ...overrides,
  })
}

/** Serves the organization's entitlements; by default both channel ones. */
export function serveEntitlements(
  entitlements: OrganizationGetOrganizationEntitlementsResponse = {
    agent_channels: true,
    agent_channels_preview: true,
  }
): void {
  jest
    .mocked(organizationGetOrganization)
    .mockResolvedValue({ id: "org-1" } as OrganizationGetOrganizationResponse)
  jest
    .mocked(organizationGetOrganizationEntitlements)
    .mockResolvedValue(entitlements)
}

/** An error the API client throws for a refused request. */
export function apiError(
  status: number,
  body: Record<string, unknown>
): Error & { status: number; body: Record<string, unknown> } {
  return Object.assign(new Error("Request failed"), { status, body })
}
