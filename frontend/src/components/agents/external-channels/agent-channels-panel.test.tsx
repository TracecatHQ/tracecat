import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentChannelRouteRead } from "@/client"
import {
  api,
  apiError,
  channelPage,
  PRESET,
  platformSlackBot,
  reservedSlackBot,
  serveBots,
  serveEntitlements,
  serveRoutes,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import {
  openProvider,
  renderPanel,
} from "@/components/agents/external-channels/channel-bot-test-render"

jest.mock("@/client")
jest.mock("@/components/agents/external-channels/slack-channel-panel", () => ({
  SlackChannelPanel: () => "Legacy Slack panel",
}))
const mockUseScopeCheck = jest.fn<boolean, [string]>(() => true)

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

beforeEach(() => {
  jest.clearAllMocks()
  mockUseScopeCheck.mockImplementation(() => true)
  serveEntitlements()
  serveBots([])
})

afterEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("AgentChannelsPanel", () => {
  it("renders the legacy panel without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    renderPanel()
    expect(await screen.findByText("Legacy Slack panel")).toBeInTheDocument()
  })

  it("renders the bot panel with the preview entitlement", async () => {
    renderPanel()
    expect(
      await screen.findByRole("group", { name: "Custom Slack app" })
    ).toBeInTheDocument()
    expect(screen.queryByText("Legacy Slack panel")).toBeNull()
  })

  it("renders the bot panel with only the preview entitlement", async () => {
    serveEntitlements({ agent_channels_preview: true })
    renderPanel()
    expect(
      await screen.findByRole("group", { name: "Custom Slack app" })
    ).toBeInTheDocument()
  })

  it("asks for a saved preset before either panel", async () => {
    renderPanel(null)
    expect(
      await screen.findByText(/Save this agent preset/)
    ).toBeInTheDocument()
    expect(screen.queryByText("Legacy Slack panel")).toBeNull()
    expect(api.listBots).not.toHaveBeenCalled()
  })
})

describe("ChannelBotsPanel", () => {
  it("lists the preset's bots in one call", async () => {
    serveBots([slackBot()])
    renderPanel()
    expect(await screen.findByText("Active")).toBeInTheDocument()
    expect(api.listBots).toHaveBeenCalledTimes(1)
  })

  it("shows a load error once", async () => {
    api.listBots.mockRejectedValue(
      apiError(400, { detail: "Something went wrong" })
    )
    renderPanel()
    expect(
      await screen.findByText("Unable to load channel settings")
    ).toBeInTheDocument()
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull()
  })

  it("says channels are unavailable when the API requires an entitlement", async () => {
    api.listBots.mockRejectedValue(
      apiError(403, {
        type: "EntitlementRequired",
        message: "Channel bots require Tracecat Enterprise",
      })
    )
    renderPanel()
    expect(await screen.findByText("Channels unavailable")).toBeInTheDocument()
    expect(
      screen.getByText("Channel bots require Tracecat Enterprise")
    ).toBeInTheDocument()
    expect(screen.queryByText(/Unable to load/)).toBeNull()
  })

  it("keeps an open dialog and its values when a refresh fails", async () => {
    serveBots([reservedSlackBot()])
    const { client } = renderPanel()
    await openProvider("Custom Slack app", "Connect")
    await userEvent.click(
      screen.getByRole("button", { name: "I already have the app" })
    )
    await userEvent.type(screen.getByLabelText("Client ID"), "123.456")

    api.listBots.mockRejectedValue(apiError(400, { detail: "Flaky" }))
    await act(async () => {
      await client.invalidateQueries()
      // Query results reach components on a later tick.
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(api.listBots).toHaveBeenCalledTimes(2)
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByLabelText("Client ID")).toHaveValue("123.456")
    expect(screen.queryByText(/Unable to load/)).toBeNull()
  })

  it("takes the card state from the bot list, not a success return", async () => {
    window.history.replaceState({}, "", "/?channel_bot_install=success")
    serveBots([reservedSlackBot()])
    renderPanel()
    const row = await screen.findByRole("group", { name: "Custom Slack app" })
    expect(within(row).getByText("Not installed")).toBeInTheDocument()
    expect(within(row).getByRole("button", { name: "Connect" })).toBeEnabled()
  })
})

function route(agentPresetId: string): AgentChannelRouteRead {
  return {
    bot_id: "platform-1",
    provider_channel_id: "C1",
    agent_preset_id: agentPresetId,
    created_at: "2026-11-15T00:00:00Z",
    updated_at: "2026-11-15T00:00:00Z",
  }
}

function servePlatform(
  installs = [platformSlackBot()],
  routes: AgentChannelRouteRead[] = []
): void {
  serveBots(installs)
  api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
  serveRoutes(routes)
}

describe("Tracecat for Slack on the Channels tab", () => {
  const CHANNELS_HREF = "/workspaces/workspace-1/agents/channels"

  async function platformCard(): Promise<HTMLElement> {
    return await screen.findByRole("group", { name: "Tracecat for Slack" })
  }

  it("leads with Tracecat for Slack, before the custom app", async () => {
    servePlatform()
    renderPanel()
    const platform = await platformCard()
    const custom = screen.getByRole("group", { name: "Custom Slack app" })
    expect(
      platform.compareDocumentPosition(custom) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      within(custom).getByRole("button", { name: "Connect" })
    ).toBeInTheDocument()
  })

  it("says this agent answers DMs and all other channels as the default", async () => {
    servePlatform()
    renderPanel()
    const card = await platformCard()
    expect(
      within(card).getByText(
        "Default agent. Answers DMs and all other channels."
      )
    ).toBeInTheDocument()
    expect(
      within(card).getByRole("link", { name: "Edit channels" })
    ).toHaveAttribute("href", CHANNELS_HREF)
    // Active is the usual case, so the card shows no status.
    expect(within(card).queryByText("Active")).toBeNull()
  })

  it("names the channels that route to this agent", async () => {
    servePlatform(
      [platformSlackBot({ agent_preset_id: "preset-2" })],
      [route(PRESET.id)]
    )
    // Named even before the bot joins it, as on the Channels page.
    api.listRoutableChannels.mockResolvedValue(
      channelPage([
        { id: "C1", name: "alerts", is_private: false, is_member: false },
      ])
    )
    renderPanel()
    const card = await platformCard()
    expect(await within(card).findByText("#alerts")).toBeInTheDocument()
    expect(within(card).getByText(/Answers in/)).toBeInTheDocument()
    expect(
      within(card).getByRole("link", { name: "Edit channels" })
    ).toHaveAttribute("href", CHANNELS_HREF)
    expect(api.listRoutes).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      botId: "platform-1",
      agentPresetId: PRESET.id,
      limit: 200,
    })
  })

  it("counts routed channels past the first three", async () => {
    const routes = ["C1", "C2", "C3", "C4", "C5"].map((id) => ({
      ...route(PRESET.id),
      provider_channel_id: id,
    }))
    servePlatform([platformSlackBot({ agent_preset_id: "preset-2" })], routes)
    renderPanel()
    const card = await platformCard()
    expect(await within(card).findByText("C3")).toBeInTheDocument()
    expect(within(card).queryByText("C4")).toBeNull()
    expect(card).toHaveTextContent("+2")
  })

  it("shows an install's status when it needs attention", async () => {
    servePlatform([
      platformSlackBot({}, { update_needed: true, missing_scopes: ["a"] }),
    ])
    renderPanel()
    const card = await platformCard()
    expect(within(card).getByText("Update needed")).toBeInTheDocument()
  })

  it("gives a member without agent:update a view link, not edit actions", async () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    servePlatform()
    renderPanel()
    const card = await platformCard()
    expect(
      within(card).getByRole("link", { name: "View channels" })
    ).toHaveAttribute("href", CHANNELS_HREF)
    expect(
      within(card).queryByRole("link", { name: "Edit channels" })
    ).toBeNull()
  })

  it("offers to assign a channel when another agent answers every channel", async () => {
    servePlatform(
      [platformSlackBot({ agent_preset_id: "preset-2" })],
      [route("preset-2")]
    )
    renderPanel()
    const card = await platformCard()
    expect(
      await within(card).findByText("Not answering in any channel yet.")
    ).toBeInTheDocument()
    expect(
      within(card).getByRole("link", { name: "Assign a channel" })
    ).toHaveAttribute("href", CHANNELS_HREF)
  })

  it("points to Add to Slack when the workspace has no install", async () => {
    servePlatform([
      platformSlackBot({ status: "disconnected", provider_app_id: null }),
    ])
    renderPanel()
    const card = await platformCard()
    expect(
      within(card).getByText("Not added to this workspace's Slack yet.")
    ).toBeInTheDocument()
    expect(
      within(card).getByRole("link", { name: "Add to Slack" })
    ).toHaveAttribute("href", CHANNELS_HREF)
    expect(api.listRoutes).not.toHaveBeenCalled()
  })

  it("shows no Tracecat for Slack card when the deployment registers no app", async () => {
    serveBots([])
    renderPanel()
    await screen.findByRole("group", { name: "Custom Slack app" })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(
      screen.queryByRole("group", { name: "Tracecat for Slack" })
    ).toBeNull()
  })
})
