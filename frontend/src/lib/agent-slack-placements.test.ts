import type {
  AgentChannelBotRead,
  AgentChannelRouteRead,
  ChannelBotChannel,
} from "@/client"
import {
  apiError,
  platformSlackBot,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import {
  type BotChannels,
  slackPlacementsByPreset,
} from "@/lib/agent-slack-placements"

jest.mock("@/client")

const INSTALL = platformSlackBot({ agent_preset_id: "triage" })
const OWN = slackBot({ id: "own-1", agent_preset_id: "phish" })

function channel(id: string, name: string): ChannelBotChannel {
  return { id, name, is_private: false, is_member: true }
}

function route(channelId: string, presetId: string): AgentChannelRouteRead {
  return {
    bot_id: INSTALL.id,
    provider_channel_id: channelId,
    agent_preset_id: presetId,
    created_at: "2026-11-15T00:00:00Z",
    updated_at: "2026-11-15T00:00:00Z",
  }
}

function listed(channels: ChannelBotChannel[]): BotChannels {
  return { channels, error: null }
}

function failed(status: number): BotChannels {
  return { channels: undefined, error: apiError(status, { detail: "no" }) }
}

function placements({
  ownBots = [],
  install = null,
  channelsByBot = {},
  routes,
  moreRoutesLoading = false,
  routesError = null,
}: {
  ownBots?: AgentChannelBotRead[]
  install?: AgentChannelBotRead | null
  channelsByBot?: Record<string, BotChannels>
  routes?: AgentChannelRouteRead[]
  moreRoutesLoading?: boolean
  routesError?: Error | null
}) {
  return slackPlacementsByPreset({
    ownBots,
    install,
    channelsByBot: new Map(Object.entries(channelsByBot)),
    routes,
    moreRoutesLoading,
    routesError,
  })
}

/** Each preset's placements, channels by ID and fallbacks by kind. */
function summary(byPreset: ReturnType<typeof placements>) {
  return Object.fromEntries(
    [...byPreset].map(([presetId, list]) => [
      presetId,
      list.map((p) => (p.kind === "channel" ? p.channelId : p.kind)),
    ])
  )
}

describe("slackPlacementsByPreset", () => {
  it("gives routed channels to their agents and the rest to the install's default", () => {
    const result = placements({
      install: INSTALL,
      channelsByBot: {
        [INSTALL.id]: listed([
          channel("C1", "soc-alerts"),
          channel("C2", "general"),
          channel("C3", "random"),
        ]),
      },
      routes: [route("C1", "enrich")],
    })
    expect(summary(result)).toEqual({
      triage: ["C2", "C3"],
      enrich: ["C1"],
    })
  })

  it("lists a custom app's channels for its own agent", () => {
    const result = placements({
      ownBots: [OWN],
      channelsByBot: { [OWN.id]: listed([channel("C7", "phish-reports")]) },
    })
    expect(summary(result)).toEqual({ phish: ["C7"] })
  })

  it("falls back to every channel when a custom app cannot list them", () => {
    const result = placements({
      ownBots: [OWN],
      channelsByBot: { [OWN.id]: failed(424) },
    })
    expect(result.get("phish")).toEqual([
      { kind: "workspace_bot", botId: OWN.id, needsReinstall: true },
    ])
    expect(
      placements({
        ownBots: [OWN],
        channelsByBot: { [OWN.id]: failed(404) },
      }).get("phish")
    ).toEqual([{ kind: "workspace_bot", botId: OWN.id, needsReinstall: false }])
  })

  it("falls back to the install's default when its channels or routes fail", () => {
    const channelsFailed = placements({
      install: INSTALL,
      channelsByBot: { [INSTALL.id]: failed(400) },
      routes: [route("C1", "enrich")],
    })
    expect(summary(channelsFailed)).toEqual({ triage: ["default"] })

    const routesFailed = placements({
      install: INSTALL,
      channelsByBot: { [INSTALL.id]: listed([channel("C1", "alerts")]) },
      routesError: apiError(400, { detail: "no" }),
    })
    expect(summary(routesFailed)).toEqual({ triage: ["default"] })
  })

  it("holds the default's channels until every route page has loaded", () => {
    const result = placements({
      install: INSTALL,
      channelsByBot: {
        [INSTALL.id]: listed([channel("C1", "alerts"), channel("C2", "b")]),
      },
      routes: [route("C1", "enrich")],
      moreRoutesLoading: true,
    })
    expect(summary(result)).toEqual({ enrich: ["C1"] })
  })

  it("shows no badge for a routed channel the install is not in", () => {
    const result = placements({
      install: INSTALL,
      channelsByBot: { [INSTALL.id]: listed([channel("C1", "alerts")]) },
      routes: [route("C1", "enrich"), route("C9", "phish")],
    })
    expect(summary(result)).toEqual({ enrich: ["C1"] })
  })

  it("names a channel once when two bots reach one agent there, sorted by name", () => {
    const own = slackBot({ id: "own-2", agent_preset_id: "triage" })
    const result = placements({
      ownBots: [own],
      install: INSTALL,
      channelsByBot: {
        [own.id]: listed([channel("C2", "zeta"), channel("C1", "alpha")]),
        [INSTALL.id]: listed([channel("C3", "mid"), channel("C1", "alpha")]),
      },
      routes: [],
    })
    expect(summary(result)).toEqual({ triage: ["C1", "C2", "C3"] })
  })

  it("ignores an install's channels without an install", () => {
    const result = placements({
      channelsByBot: { [INSTALL.id]: listed([channel("C1", "alerts")]) },
      routes: [route("C1", "enrich")],
    })
    expect(result.size).toBe(0)
  })
})
