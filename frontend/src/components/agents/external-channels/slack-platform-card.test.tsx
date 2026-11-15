import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  api,
  platformSlackBot,
  serveBots,
  serveEntitlements,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderChannelsPage } from "@/components/agents/external-channels/channel-bot-test-render"
import { goToChannelBotAuthorization } from "@/components/agents/external-channels/channel-bot-utils"
import { toast } from "@/components/ui/use-toast"

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
const PAGE_PATH = "/workspaces/workspace-1/agents/channels"

function servePlatform(installs = [platformSlackBot()]): void {
  serveBots(installs)
  api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
}

async function platformRow(): Promise<HTMLElement> {
  return await screen.findByRole("group", { name: "Tracecat for Slack" })
}

async function chooseDefaultAgent(
  row: HTMLElement,
  agent: string
): Promise<void> {
  await userEvent.click(
    within(row).getByRole("combobox", { name: "Default agent" })
  )
  await userEvent.click(await screen.findByRole("option", { name: agent }))
}

beforeAll(() => {
  // Radix Select captures the pointer, which jsdom does not implement.
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false)
  HTMLElement.prototype.releasePointerCapture = jest.fn()
})

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([])
  window.history.replaceState({}, "", PAGE_PATH)
})

afterEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("Channels page", () => {
  it("says Tracecat for Slack is unavailable when the deployment registers no Slack app", async () => {
    renderChannelsPage()
    expect(
      await screen.findByText(
        "Tracecat for Slack is not available in this workspace."
      )
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("group", { name: "Tracecat for Slack" })
    ).toBeNull()
  })

  it("is unavailable without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    servePlatform()
    renderChannelsPage()
    expect(
      await screen.findByText(
        "Tracecat for Slack is not available in this workspace."
      )
    ).toBeInTheDocument()
    expect(api.listPlatformApps).not.toHaveBeenCalled()
    expect(api.listBots).not.toHaveBeenCalled()
  })

  it("adds Tracecat to Slack with the chosen default agent and returns here", async () => {
    servePlatform([])
    api.installPlatformBot.mockResolvedValue({
      bot: platformSlackBot({
        status: "needs_reconnect",
        provider_app_id: null,
      }),
      authorization_url: AUTHORIZE_URL,
    })
    renderChannelsPage()
    const row = await platformRow()
    const add = within(row).getByRole("button", { name: "Add to Slack" })
    expect(add).toBeDisabled()

    await chooseDefaultAgent(row, "Phishing")
    await userEvent.click(add)

    expect(api.installPlatformBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        provider: "slack",
        return_url: `http://localhost${PAGE_PATH}`,
        bot_id: undefined,
        agent_preset_id: "preset-2",
      },
    })
    await waitFor(() => expect(goTo).toHaveBeenCalledWith(AUTHORIZE_URL))
  })

  it("adds a disconnected install again with its last default agent", async () => {
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
    renderChannelsPage()
    const row = await platformRow()
    await waitFor(() =>
      expect(
        within(row).getByRole("combobox", { name: "Default agent" })
      ).toHaveTextContent("Phishing")
    )
    await userEvent.click(
      within(row).getByRole("button", { name: "Add to Slack" })
    )
    expect(api.installPlatformBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({
        bot_id: "platform-1",
        agent_preset_id: "preset-2",
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
    renderChannelsPage()
    const row = await platformRow()
    await userEvent.click(
      within(row).getByRole("button", { name: "Reconnect" })
    )
    expect(api.installPlatformBot.mock.calls[0][0].requestBody).toMatchObject({
      bot_id: "platform-1",
      agent_preset_id: undefined,
    })
  })

  it("manages a connected install: routes, pause and disconnect", async () => {
    servePlatform()
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Active")).toBeInTheDocument()
    expect(within(row).getByText("Connected to Slack")).toBeInTheDocument()
    expect(
      await within(row).findByRole("combobox", { name: "Default agent" })
    ).toBeInTheDocument()
    expect(within(row).getByLabelText("Channel ID")).toBeInTheDocument()
    expect(within(row).getByRole("button", { name: "Pause" })).toBeEnabled()
    expect(
      within(row).getByRole("button", { name: "Disconnect" })
    ).toBeEnabled()
    expect(
      within(row).queryByRole("button", { name: "Add to Slack" })
    ).toBeNull()
  })

  it("explains a Slack workspace another Tracecat workspace holds", async () => {
    window.history.replaceState(
      {},
      "",
      `${PAGE_PATH}?channel_bot_install=error&channel_bot_message=${encodeURIComponent(
        "Tracecat is already installed in this Slack workspace. Disconnect that install, or ask a Slack admin to remove the app in Slack"
      )}`
    )
    servePlatform([])
    renderChannelsPage()
    const row = await platformRow()
    expect(
      within(row).getByText(
        "This Slack workspace is already connected to another Tracecat workspace"
      )
    ).toBeInTheDocument()
    expect(toast).not.toHaveBeenCalled()
    expect(window.location.search).toBe("")
  })
})
