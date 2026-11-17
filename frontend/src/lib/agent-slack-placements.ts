import type {
  AgentChannelBotRead,
  AgentChannelRouteRead,
  ChannelBotChannel,
} from "@/client"
import {
  byChannelName,
  isChannelListingNeedsReinstall,
} from "@/components/agents/external-channels/channel-bot-utils"

/**
 * Where an agent answers in Slack: one channel its bot is in or routes to it,
 * or, when a bot's channels cannot be listed, every channel of its own Slack
 * app or the platform install's channels without a route.
 */
export type SlackPlacement =
  | {
      kind: "workspace_bot"
      botId: string
      /** The app lacks the scopes to list its channels until reinstalled. */
      needsReinstall: boolean
    }
  | { kind: "default" }
  | { kind: "channel"; channel: ChannelBotChannel }

/** A bot's first page of channels, or why it could not be listed. */
export type BotChannels = {
  channels: ChannelBotChannel[] | undefined
  error: Error | null
}

/**
 * Where each agent answers in Slack, by preset ID. A platform install's routed
 * channels go to their agents and the rest to its default agent; a bot whose
 * channels cannot be listed falls back to answering everywhere.
 */
export function slackPlacementsByPreset({
  ownBots,
  install,
  channelsByBot,
  routes,
  routesError,
}: {
  /** The workspace's running custom-app bots. */
  ownBots: AgentChannelBotRead[]
  /** The running platform install, if any. */
  install: AgentChannelBotRead | null
  channelsByBot: Map<string, BotChannels>
  /** Every route of the install, once loaded. */
  routes: AgentChannelRouteRead[] | undefined
  routesError: Error | null
}): Map<string, SlackPlacement[]> {
  const byPreset = new Map<string, SlackPlacement[]>()
  function add(presetId: string, placement: SlackPlacement): void {
    const placements = byPreset.get(presetId) ?? []
    // The same channel can reach one agent through two bots.
    if (
      placement.kind === "channel" &&
      placements.some(
        (p) => p.kind === "channel" && p.channel.id === placement.channel.id
      )
    ) {
      return
    }
    byPreset.set(presetId, [...placements, placement])
  }
  function addChannels(presetId: string, channels: ChannelBotChannel[]): void {
    for (const channel of channels) {
      add(presetId, { kind: "channel", channel })
    }
  }

  for (const bot of ownBots) {
    const listed = channelsByBot.get(bot.id)
    if (listed?.channels) {
      addChannels(bot.agent_preset_id, listed.channels)
    } else if (listed?.error) {
      add(bot.agent_preset_id, {
        kind: "workspace_bot",
        botId: bot.id,
        needsReinstall: isChannelListingNeedsReinstall(listed.error),
      })
    }
  }
  if (!install) {
    return sortChannels(byPreset)
  }

  const listed = channelsByBot.get(install.id)
  if (listed?.channels && routes) {
    const routed = new Set(routes.map((route) => route.provider_channel_id))
    addChannels(
      install.agent_preset_id,
      listed.channels.filter((channel) => !routed.has(channel.id))
    )
  } else if (listed?.error || routesError) {
    add(install.agent_preset_id, { kind: "default" })
  }
  // A routed channel the bot is not in has no name to show, so it shows nothing.
  const channelsById = new Map((listed?.channels ?? []).map((c) => [c.id, c]))
  const routedChannels = (routes ?? []).flatMap((route) => {
    const channel = channelsById.get(route.provider_channel_id)
    return channel ? [{ presetId: route.agent_preset_id, channel }] : []
  })
  for (const { presetId, channel } of routedChannels) {
    add(presetId, { kind: "channel", channel })
  }
  return sortChannels(byPreset)
}

/** Each agent's channels in name order; other placements keep their slots. */
function sortChannels(
  byPreset: Map<string, SlackPlacement[]>
): Map<string, SlackPlacement[]> {
  for (const [presetId, placements] of byPreset) {
    const channels = placements
      .flatMap((p) => (p.kind === "channel" ? [p] : []))
      .sort((a, b) => byChannelName(a.channel, b.channel))
    let next = 0
    byPreset.set(
      presetId,
      placements.map((p) => (p.kind === "channel" ? channels[next++] : p))
    )
  }
  return byPreset
}
