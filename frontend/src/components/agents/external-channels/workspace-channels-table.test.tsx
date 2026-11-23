import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  api,
  platformSlackBot,
  routePage,
  serveBots,
  serveEntitlements,
  serveWorkspaceRoutes,
  slackBot,
  workspaceRoute,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderChannelsPage } from "@/components/agents/external-channels/channel-bot-test-render"

jest.mock("@/client")
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const CUSTOM = slackBot({ id: "custom-1", agent_preset_id: "preset-2" })

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([platformSlackBot(), CUSTOM])
  api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
  serveWorkspaceRoutes([
    workspaceRoute({ provider_channel_id: "C1", channel_name: "soc-alerts" }),
    workspaceRoute({
      provider_channel_id: "G1",
      channel_name: "incident",
      is_private: true,
      agent_preset_id: "preset-2",
      agent_name: "Phishing",
    }),
    workspaceRoute({
      provider_channel_id: "C2",
      channel_name: null,
      is_member: false,
    }),
    workspaceRoute({
      bot_id: CUSTOM.id,
      app_owner: "workspace",
      routed: false,
      provider_channel_id: "C9",
      channel_name: "phishing-reports",
      agent_preset_id: "preset-2",
      agent_name: "Phishing",
    }),
  ])
})

async function table(): Promise<HTMLElement> {
  return await screen.findByRole("table", { name: "Channels" })
}

function rowOf(container: HTMLElement, text: string): HTMLElement {
  const row = within(container).getByText(text).closest("tr")
  if (!row) throw new Error(`No row for ${text}`)
  return row
}

describe("Workspace channels table", () => {
  it("lists every bot's channels with their agent and status, custom apps included", async () => {
    renderChannelsPage()
    const grid = await table()

    await within(grid).findByText("soc-alerts")
    expect(
      within(grid).getByRole("row", { name: /Tracecat for Slack/ })
    ).toHaveTextContent("3 channels · 2 agents")
    const custom = within(grid).getByRole("row", { name: /Custom Slack app/ })
    expect(custom).toHaveTextContent("Custom")
    expect(custom).toHaveTextContent("1 channel · Phishing")

    expect(rowOf(grid, "Everywhere else")).toHaveTextContent("Triage")
    expect(rowOf(grid, "incident")).toHaveTextContent("Phishing")
    expect(
      within(rowOf(grid, "incident")).getByRole("img", {
        name: "Private channel",
      })
    ).toBeInTheDocument()
    expect(rowOf(grid, "soc-alerts")).toHaveTextContent("Active")
    // Not in the channel: its ID, and how to add the bot.
    expect(rowOf(grid, "C2")).toHaveTextContent(
      "Not in channel · /invite @Tracecat"
    )
    expect(rowOf(grid, "phishing-reports")).toHaveTextContent("Phishing")
    expect(api.listWorkspaceRoutes).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      q: undefined,
      limit: 200,
      cursor: null,
    })
  })

  it("searches channel names on the server and hides bots without a match", async () => {
    renderChannelsPage()
    const grid = await table()
    await within(grid).findByText("soc-alerts")

    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search channels" }),
      "#phish"
    )

    await waitFor(() =>
      expect(api.listWorkspaceRoutes).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "#phish" })
      )
    )
    await waitFor(() =>
      expect(within(grid).queryByText("soc-alerts")).toBeNull()
    )
    expect(within(grid).getByText("phishing-reports")).toBeInTheDocument()
    expect(
      within(grid).queryByRole("row", { name: /Tracecat for Slack/ })
    ).toBeNull()
    expect(within(grid).queryByText("Everywhere else")).toBeNull()

    await userEvent.clear(
      screen.getByRole("searchbox", { name: "Search channels" })
    )
    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search channels" }),
      "nothing"
    )
    expect(
      await within(grid).findByText(
        "No channels match. Invite a bot to the channel, then add it here."
      )
    ).toBeInTheDocument()
  })

  it("keeps a bot's channels by ID when Slack could not name them", async () => {
    serveWorkspaceRoutes([
      workspaceRoute({
        channel_name: null,
        is_private: null,
        is_member: null,
        name_unavailable: true,
      }),
    ])
    renderChannelsPage()
    const grid = await table()
    await within(grid).findByText("C1")

    const row = rowOf(grid, "C1")
    expect(row).toHaveTextContent("Name unavailable")
    expect(row).not.toHaveTextContent("Active")
  })

  it("lands a new Tracecat install with its default agent and the invite hint", async () => {
    serveBots([platformSlackBot()])
    serveWorkspaceRoutes([])
    renderChannelsPage()
    const grid = await table()

    expect(
      await within(grid).findByText(
        /in a channel, then give it its own agent below/
      )
    ).toBeInTheDocument()
    expect(within(grid).getByText("/invite @Tracecat")).toBeInTheDocument()
    expect(rowOf(grid, "Everywhere else")).toHaveTextContent("Triage")
  })

  it("says Slack would not list a bot's channels instead of asking to invite it", async () => {
    api.listWorkspaceRoutes.mockResolvedValue({
      ...routePage([workspaceRoute()]),
      listing_failures: [{ bot_id: CUSTOM.id, needs_reinstall: true }],
    })
    renderChannelsPage()
    const grid = await table()

    expect(
      await within(grid).findByText(
        "Slack won't list this bot's channels until its app is reinstalled."
      )
    ).toBeInTheDocument()
    expect(within(grid).queryByText(/Not in any channel yet/)).toBeNull()
  })

  it("waits for every page before saying a bot is in no channel", async () => {
    api.listWorkspaceRoutes.mockResolvedValue(
      routePage([workspaceRoute()], "next")
    )
    renderChannelsPage()
    const grid = await table()
    await within(grid).findByText("soc-alerts")

    expect(within(grid).queryByText(/Not in any channel yet/)).toBeNull()
  })

  it("pages in more channels", async () => {
    api.listWorkspaceRoutes
      .mockResolvedValueOnce(routePage([workspaceRoute()], "next"))
      .mockResolvedValueOnce(
        routePage([
          workspaceRoute({ provider_channel_id: "C7", channel_name: "later" }),
        ])
      )
    renderChannelsPage()
    const grid = await table()
    await within(grid).findByText("soc-alerts")

    await userEvent.click(screen.getByRole("button", { name: "Load more" }))

    expect(await within(grid).findByText("later")).toBeInTheDocument()
    expect(api.listWorkspaceRoutes).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "next" })
    )
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull()
  })

  it("is not shown without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    renderChannelsPage()
    expect(
      await screen.findByText(
        "Tracecat for Slack is not available in this workspace."
      )
    ).toBeInTheDocument()
    expect(screen.queryByRole("table", { name: "Channels" })).toBeNull()
    expect(api.listWorkspaceRoutes).not.toHaveBeenCalled()
  })
})
