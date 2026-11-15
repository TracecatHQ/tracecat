import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  api,
  PRESET,
  platformSlackBot,
  serveBots,
  serveEntitlements,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderPanel } from "@/components/agents/external-channels/channel-bot-test-render"
import { goToChannelBotAuthorization } from "@/components/agents/external-channels/channel-bot-utils"

jest.mock("@/client")
jest.mock("@/components/ui/use-toast", () => ({
  ...jest.requireActual("@/components/ui/use-toast"),
  toast: jest.fn(),
}))
jest.mock("@/components/agents/external-channels/channel-bot-utils", () => ({
  ...jest.requireActual(
    "@/components/agents/external-channels/channel-bot-utils"
  ),
  goToChannelBotAuthorization: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const goTo = jest.mocked(goToChannelBotAuthorization)
const AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize?client_id=123.456"

function servePlatform(installs = [platformSlackBot()]): void {
  serveBots(installs)
  api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
}

async function platformRow(): Promise<HTMLElement> {
  return await screen.findByRole("group", { name: "Tracecat for Slack" })
}

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([])
})

describe("SlackPlatformCard", () => {
  it("is hidden when the deployment registers no Slack app", async () => {
    renderPanel()
    await screen.findByRole("group", { name: "Custom Slack app" })
    expect(
      screen.queryByRole("group", { name: "Tracecat for Slack" })
    ).toBeNull()
  })

  it("adds Tracecat to Slack with this agent as the default", async () => {
    servePlatform([])
    api.installPlatformBot.mockResolvedValue({
      bot: platformSlackBot({
        status: "needs_reconnect",
        provider_app_id: null,
      }),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    const row = await platformRow()
    await userEvent.click(
      within(row).getByRole("button", { name: "Add to Slack" })
    )
    expect(api.installPlatformBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({
        provider: "slack",
        agent_preset_id: PRESET.id,
      }),
    })
    await waitFor(() => expect(goTo).toHaveBeenCalledWith(AUTHORIZE_URL))
  })

  it("makes this agent the default of an install another agent answers", async () => {
    servePlatform([platformSlackBot({ agent_preset_id: "preset-2" })])
    api.updateBot.mockResolvedValue(platformSlackBot())
    renderPanel()
    const row = await platformRow()
    expect(within(row).getByText(/another agent answers/)).toBeInTheDocument()
    await userEvent.click(
      within(row).getByRole("button", { name: "Make default" })
    )
    expect(api.updateBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      botId: "platform-1",
      requestBody: { agent_preset_id: PRESET.id },
    })
  })

  it("adds a disconnected install again with this agent as the default", async () => {
    servePlatform([
      platformSlackBot({
        status: "disconnected",
        provider_app_id: null,
        agent_preset_id: "preset-2",
      }),
    ])
    api.installPlatformBot.mockResolvedValue({
      bot: platformSlackBot(),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    const row = await platformRow()
    await userEvent.click(
      within(row).getByRole("button", { name: "Add to Slack" })
    )
    expect(api.installPlatformBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({
        bot_id: "platform-1",
        agent_preset_id: PRESET.id,
      }),
    })
  })

  it("reconnects an install without changing its default agent", async () => {
    servePlatform([
      platformSlackBot({
        status: "needs_reconnect",
        agent_preset_id: "preset-2",
      }),
    ])
    api.installPlatformBot.mockResolvedValue({
      bot: platformSlackBot(),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    const row = await platformRow()
    await userEvent.click(
      within(row).getByRole("button", { name: "Reconnect" })
    )
    expect(api.installPlatformBot.mock.calls[0][0].requestBody).toMatchObject({
      bot_id: "platform-1",
      agent_preset_id: undefined,
    })
  })

  it("leaves the custom app card unconnected beside the install", async () => {
    servePlatform()
    renderPanel()
    const row = await platformRow()
    expect(within(row).getByText("Active")).toBeInTheDocument()
    const custom = await screen.findByRole("group", {
      name: "Custom Slack app",
    })
    expect(
      within(custom).getByRole("button", { name: "Connect" })
    ).toBeInTheDocument()
  })
})
