import {
  type AgentChannelBotRead,
  type AgentPresetRead,
  agentChannelBotsCreateBot,
  agentChannelBotsDisconnectBot,
  agentChannelBotsInstallBot,
  agentChannelBotsListBots,
  agentChannelBotsUpdateBot,
  type ChannelBotDetails,
  type OrganizationGetOrganizationEntitlementsResponse,
  type OrganizationGetOrganizationResponse,
  organizationGetOrganization,
  organizationGetOrganizationEntitlements,
} from "@/client"

// Test files call `jest.mock("@/client")`; these are its channel bot calls.
export const api = {
  listBots: jest.mocked(agentChannelBotsListBots),
  createBot: jest.mocked(agentChannelBotsCreateBot),
  installBot: jest.mocked(agentChannelBotsInstallBot),
  updateBot: jest.mocked(agentChannelBotsUpdateBot),
  disconnectBot: jest.mocked(agentChannelBotsDisconnectBot),
}

export const WORKSPACE_ID = "workspace-1"
export const PRESET = { id: "preset-1", name: "Triage" } as AgentPresetRead
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

/** Serves `bots` from the list call; mutations change what it serves. */
export function serveBots(bots: AgentChannelBotRead[]): void {
  api.listBots.mockResolvedValue({ items: bots, next_cursor: null })
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
