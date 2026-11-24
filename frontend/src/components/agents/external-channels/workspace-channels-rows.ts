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

const UNKNOWN_AGENT = "Unknown agent"

/** How the Channels table groups its rows. */
export type ChannelsGroupBy = "bot" | "agent" | "none"

/** A column of the Channels table. */
export type ChannelsColumn = "channel" | "agent" | "bot" | "status"

/** The data columns each grouping shows, before the action column. */
export const CHANNELS_LAYOUT: Record<ChannelsGroupBy, ChannelsColumn[]> = {
  bot: ["channel", "agent", "status"],
  agent: ["channel", "bot", "status"],
  none: ["channel", "bot", "agent", "status"],
}

/** What a row's status cell says, and the value its facet filters on. */
export type ChannelRowStatus =
  | "Active"
  | "Not in channel"
  | "Unknown"
  | "Default"
  | "Paused"
  | "Needs reconnect"

/**
 * What a row is: a platform install's catch-all, a channel with its own agent
 * (removable), or a channel answered by the bot's default agent.
 */
export type ChannelRowKind = "default" | "routed" | "unrouted"

type RowBase = {
  key: string
  bot: AgentChannelBotRead
  botTitle: string
  /** The channel's name, its ID when unnamed, or "Everywhere else". */
  label: string
  agentId: string
  agentName: string
  status: ChannelRowStatus
  provider: string
  app: "Tracecat" | "Custom"
}

/** One row of the Channels table: a bot's channel, or its "Everywhere else". */
export type ChannelTableRow =
  | (RowBase & { kind: "default" })
  | (RowBase & {
      kind: "routed" | "unrouted"
      channel: AgentChannelWorkspaceRouteRead
    })

/** The facet columns, each read from one row field. */
export const FACETS = [
  { column: "provider", title: "Provider", value: (r: RowBase) => r.provider },
  { column: "app", title: "App", value: (r: RowBase) => r.app },
  { column: "agent", title: "Agent", value: (r: RowBase) => r.agentName },
  { column: "status", title: "Status", value: (r: RowBase) => r.status },
] as const

/** A bot's name in the table and its sheet. */
export function channelBotTitle(bot: AgentChannelBotRead): string {
  return bot.app_owner === "platform"
    ? PLATFORM_SLACK_TITLE
    : CUSTOM_SLACK_TITLE
}

/** Whether a row is Tracecat's app, whose channels each get an agent. */
export function isPlatformRow(row: ChannelTableRow): boolean {
  return row.bot.app_owner === "platform"
}

/** A stopped bot's state, which every one of its rows shows; null while it runs. */
function stoppedStatus(bot: AgentChannelBotRead): ChannelRowStatus | null {
  if (bot.status === "paused") {
    return "Paused"
  }
  return bot.status === "needs_reconnect" ? "Needs reconnect" : null
}

function channelStatus(
  bot: AgentChannelBotRead,
  channel: AgentChannelWorkspaceRouteRead
): ChannelRowStatus {
  const stopped = stoppedStatus(bot)
  if (stopped) {
    return stopped
  }
  if (channel.is_member === true) {
    return "Active"
  }
  return channel.is_member === false ? "Not in channel" : "Unknown"
}

/** Every bot's rows: a platform install's default first, then its channels. */
export function buildChannelRows(
  bots: AgentChannelBotRead[],
  channels: AgentChannelWorkspaceRouteRead[],
  agentName: (id: string) => string
): ChannelTableRow[] {
  return bots.flatMap((bot): ChannelTableRow[] => {
    const platform = bot.app_owner === "platform"
    const base = {
      bot,
      botTitle: channelBotTitle(bot),
      provider: CHANNEL_BOT_PROVIDER_NAMES[bot.provider],
      app: platform ? ("Tracecat" as const) : ("Custom" as const),
    }
    const rows: ChannelTableRow[] = channels
      .filter((channel) => channel.bot_id === bot.id)
      .map((channel) => ({
        ...base,
        kind: channel.routed ? "routed" : "unrouted",
        channel,
        key: `${bot.id}:${channel.provider_channel_id}`,
        label: channel.channel_name ?? channel.provider_channel_id,
        agentId: channel.agent_preset_id,
        agentName: channel.agent_name || UNKNOWN_AGENT,
        status: channelStatus(bot, channel),
      }))
    if (!platform) {
      return rows
    }
    return [
      {
        ...base,
        kind: "default",
        key: `${bot.id}:default`,
        label: EVERYWHERE_ELSE,
        agentId: bot.agent_preset_id,
        agentName: agentName(bot.agent_preset_id) || UNKNOWN_AGENT,
        status: stoppedStatus(bot) ?? "Default",
      },
      ...rows,
    ]
  })
}

/** Whether a row matches the search box, by name or channel ID; a leading `#` is ignored. */
export function matchesChannelSearch(
  row: ChannelTableRow,
  search: string
): boolean {
  const q = search.trim().toLowerCase().replace(/^#/, "")
  const id = row.kind === "default" ? "" : row.channel.provider_channel_id
  return row.label.toLowerCase().includes(q) || id.toLowerCase().includes(q)
}

/** Rows of a bot, the install's default first. */
function defaultFirst(rows: ChannelTableRow[]): ChannelTableRow[] {
  return [
    ...rows.filter((row) => row.kind === "default"),
    ...rows.filter((row) => row.kind !== "default"),
  ]
}

/** The shown rows of each bot, in the bots' order. */
export function groupByBot(
  bots: AgentChannelBotRead[],
  rows: ChannelTableRow[]
): { bot: AgentChannelBotRead; rows: ChannelTableRow[] }[] {
  return bots.map((bot) => ({
    bot,
    rows: defaultFirst(rows.filter((row) => row.bot.id === bot.id)),
  }))
}

/** The shown rows of each agent, by agent ID, in name order. */
export function groupByAgent(
  rows: ChannelTableRow[]
): { agentId: string; agentName: string; rows: ChannelTableRow[] }[] {
  const groups = new Map<
    string,
    { agentId: string; agentName: string; rows: ChannelTableRow[] }
  >()
  for (const row of rows) {
    const group = groups.get(row.agentId) ?? {
      agentId: row.agentId,
      agentName: row.agentName,
      rows: [],
    }
    group.rows.push(row)
    groups.set(row.agentId, group)
  }
  return [...groups.values()].sort((a, b) =>
    a.agentName.localeCompare(b.agentName)
  )
}

/** A bot's channel count and agents, as its group row sums them. */
export function botSummary(
  bot: AgentChannelBotRead,
  rows: ChannelTableRow[],
  defaultAgentName: string
): string {
  const count = channelCount(
    rows.filter((row) => row.kind !== "default").length
  )
  if (bot.app_owner !== "platform") {
    return defaultAgentName ? `${count} · ${defaultAgentName}` : count
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
