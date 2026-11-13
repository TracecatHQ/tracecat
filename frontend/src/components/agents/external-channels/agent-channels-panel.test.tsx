import { act, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  api,
  apiError,
  reservedSlackBot,
  serveBots,
  serveEntitlements,
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
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

beforeEach(() => {
  jest.clearAllMocks()
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
