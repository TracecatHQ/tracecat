import type {
  AgentChannelBotRead,
  AgentChannelWorkspaceRouteRead,
} from "@/client"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  CHANNEL_BOT_STATES,
  channelBotState,
  PLATFORM_SLACK_TITLE,
} from "@/components/agents/external-channels/channel-bot-utils"

/** A custom app's name: none is stored, and asking Slack is another call per bot. */
export const CUSTOM_SLACK_TITLE = "Custom Slack app"

/** The name a platform install's catch-all row goes by. */
export const EVERYWHERE_ELSE = "Everywhere else"

/** How the Channels table groups its rows. */
export type ChannelsGroupBy = "bot" | "agent" | "none"

/** What a row's status cell says, and the value its facet filters on. */
export type ChannelRowStatus =
  | "Active"
  | "Not in channel"
  | "Unknown"
  | "Default"
  | "Paused"
  | "Needs reconnect"

/** One row of the Channels table: a bot's channel, or its "Everywhere else". */
export type ChannelTableRow = {
  key: string
  bot: AgentChannelBotRead
  botTitle: string
  /** Tracecat's own app, whose channels each get an agent. */
  platform: boolean
  /** A channel route, or null for the install's default. */
  route: AgentChannelWorkspaceRouteRead | null
  /** The channel's name, its ID when unnamed, or "Everywhere else". */
  label: string
  agentId: string
  agentName: string
  status: ChannelRowStatus
  provider: string
  app: "Tracecat" | "Custom"
}

/** A bot's name in the table and its sheet. */
export function channelBotTitle(bot: AgentChannelBotRead): string {
  return bot.app_owner === "platform"
    ? PLATFORM_SLACK_TITLE
    : CUSTOM_SLACK_TITLE
}

function routeStatus(
  bot: AgentChannelBotRead,
  route: AgentChannelWorkspaceRouteRead
): ChannelRowStatus {
  if (bot.status === "paused") {
    return "Paused"
  }
  if (bot.status === "needs_reconnect") {
    return "Needs reconnect"
  }
  if (route.is_member === true) {
    return "Active"
  }
  return route.is_member === false ? "Not in channel" : "Unknown"
}

/** Every bot's rows: a platform install's default first, then its channels. */
export function buildChannelRows(
  bots: AgentChannelBotRead[],
  routes: AgentChannelWorkspaceRouteRead[],
  agentName: (id: string) => string
): ChannelTableRow[] {
  return bots.flatMap((bot) => {
    const platform = bot.app_owner === "platform"
    const common = {
      bot,
      botTitle: channelBotTitle(bot),
      platform,
      provider: CHANNEL_BOT_PROVIDER_NAMES[bot.provider],
      app: platform ? ("Tracecat" as const) : ("Custom" as const),
    }
    const channels = routes
      .filter((route) => route.bot_id === bot.id)
      .map(
        (route): ChannelTableRow => ({
          ...common,
          key: `${bot.id}:${route.provider_channel_id}`,
          route,
          label: route.channel_name ?? route.provider_channel_id,
          agentId: route.agent_preset_id,
          agentName: route.agent_name,
          status: routeStatus(bot, route),
        })
      )
    if (!platform) {
      return channels
    }
    const fallback: ChannelTableRow = {
      ...common,
      key: `${bot.id}:default`,
      route: null,
      label: EVERYWHERE_ELSE,
      agentId: bot.agent_preset_id,
      agentName: agentName(bot.agent_preset_id),
      status: "Default",
    }
    return [fallback, ...channels]
  })
}

/** A bot's channel count and agents, as its group row sums them. */
export function botSummary(
  bot: AgentChannelBotRead,
  rows: ChannelTableRow[]
): string {
  const channels = rows.filter((row) => row.route !== null)
  const count = channelCount(channels.length)
  if (bot.app_owner !== "platform") {
    return `${count} · ${rows[0]?.agentName ?? ""}`.replace(/ · $/, "")
  }
  const agents = new Set(rows.map((row) => row.agentId)).size
  return `${count} · ${agents === 1 ? "1 agent" : `${agents} agents`}`
}

/** `n channels`, singular for one. */
export function channelCount(n: number): string {
  return n === 1 ? "1 channel" : `${n} channels`
}

/** Whether a bot is installed and so answers somewhere. */
export function isShownBot(bot: AgentChannelBotRead): boolean {
  return CHANNEL_BOT_STATES[channelBotState(bot, bot.provider_app_id != null)]
    .installed
}
