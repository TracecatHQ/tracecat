import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  api,
  apiError,
  platformSlackBot,
  serveBots,
  serveEntitlements,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderChannelsPage } from "@/components/agents/external-channels/channel-bot-test-render"
import { goToChannelBotAuthorization } from "@/components/agents/external-channels/channel-bot-utils"
import { toast } from "@/components/ui/use-toast"

const mockUseScopeCheck = jest.fn<boolean, [string]>(() => true)

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
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
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
  mockUseScopeCheck.mockImplementation(() => true)
  serveEntitlements()
  serveBots([])
  window.history.replaceState({}, "", PAGE_PATH)
})

afterEach(() => {
  window.history.replaceState({}, "", "/")
})

describe("Channels page", () => {
  it("points to Admin › Slack app when the deployment registers no Slack app", async () => {
    renderChannelsPage()
    expect(
      await screen.findByText(
        "Tracecat for Slack isn't set up on this instance yet. An admin sets it up in Admin › Slack app."
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

  it("manages a connected install: routes, then pauses it from a menu that offers Disconnect", async () => {
    servePlatform()
    api.updateBot.mockResolvedValue(platformSlackBot({ status: "paused" }))
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Active")).toBeInTheDocument()
    expect(within(row).getByText("Tracecat for Slack")).toBeInTheDocument()
    expect(within(row).queryByText("Connected to Slack")).toBeNull()
    expect(
      await within(row).findByRole("combobox", { name: "Default agent" })
    ).toBeInTheDocument()
    expect(
      within(row).getByRole("combobox", { name: "Channel" })
    ).toBeInTheDocument()
    expect(
      within(row).queryByRole("button", { name: "Add to Slack" })
    ).toBeNull()

    await userEvent.click(
      within(row).getByRole("button", { name: "More actions" })
    )
    expect(
      screen.getByRole("menuitem", { name: "Disconnect" })
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole("menuitem", { name: "Pause" }))
    expect(api.updateBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      botId: "platform-1",
      requestBody: { status: "paused" },
    })
  })

  it("sets who can use the install", async () => {
    servePlatform()
    api.updateBot.mockResolvedValue(
      platformSlackBot({ channel_access_policy: "linked_only" })
    )
    renderChannelsPage()
    const row = await platformRow()
    const select = await within(row).findByRole("combobox", {
      name: "Who can use this bot",
    })
    expect(select).toHaveTextContent(
      "Anyone can message; linked members approve"
    )
    await userEvent.click(select)
    await userEvent.click(
      await screen.findByRole("option", { name: "Only linked members" })
    )
    await waitFor(() =>
      expect(api.updateBot).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        botId: "platform-1",
        requestBody: { channel_access_policy: "linked_only" },
      })
    )
  })

  it("tells a member without agent:update who can add Tracecat, with no dead controls", async () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    servePlatform([])
    renderChannelsPage()
    const row = await platformRow()
    expect(
      within(row).getByText(
        "Ask someone who can edit agents to add Tracecat to Slack."
      )
    ).toBeInTheDocument()
    expect(
      within(row).queryByRole("button", { name: "Add to Slack" })
    ).toBeNull()
    expect(
      within(row).queryByRole("combobox", { name: "Default agent" })
    ).toBeNull()
  })

  it("says a paused install ignores Slack and resumes it beside its status", async () => {
    servePlatform([platformSlackBot({ status: "paused" })])
    api.updateBot.mockResolvedValue(platformSlackBot())
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Paused")).toBeInTheDocument()
    expect(
      within(row).getByText("Tracecat ignores Slack messages until you resume.")
    ).toBeInTheDocument()
    await userEvent.click(within(row).getByRole("button", { name: "Resume" }))
    expect(api.updateBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      botId: "platform-1",
      requestBody: { status: "active" },
    })
  })

  it("tells a member without agent:update who can resume a paused install", async () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    servePlatform([platformSlackBot({ status: "paused" })])
    renderChannelsPage()
    const row = await platformRow()
    expect(
      within(row).getByText(
        "Tracecat ignores Slack messages until it's resumed. Ask someone who can edit agents to resume it."
      )
    ).toBeInTheDocument()
    expect(within(row).queryByRole("button", { name: "Resume" })).toBeNull()
  })

  it("flags a paused install missing scopes; a reconnect clears both", async () => {
    servePlatform([
      platformSlackBot(
        { status: "paused" },
        { update_needed: true, missing_scopes: ["channels:read"] }
      ),
    ])
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Update needed")).toBeInTheDocument()
    expect(within(row).queryByText("Paused")).toBeNull()
    expect(within(row).getByText(/which also resumes it\./)).toBeInTheDocument()
    expect(within(row).getByRole("button", { name: "Reconnect" })).toBeEnabled()
    // Resume stays reachable without a Slack approval.
    await userEvent.click(
      within(row).getByRole("button", { name: "More actions" })
    )
    expect(screen.getByRole("menuitem", { name: "Resume" })).toBeInTheDocument()
  })

  it("titles an install that needs reconnecting by its name, not as connected", async () => {
    servePlatform([platformSlackBot({ status: "needs_reconnect" })])
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Tracecat for Slack")).toBeInTheDocument()
    expect(within(row).queryByText("Connected to Slack")).toBeNull()
    expect(within(row).getByText("Needs reconnect")).toBeInTheDocument()
    expect(
      within(row).getByText(
        "Tracecat isn't answering in Slack. Reconnect to restore it."
      )
    ).toBeInTheDocument()
    expect(within(row).getByRole("button", { name: "Reconnect" })).toBeEnabled()
  })

  it("flags an install missing scopes and reconnects it to request them", async () => {
    servePlatform([
      platformSlackBot(
        {},
        {
          update_needed: true,
          missing_scopes: ["channels:read", "groups:read"],
        }
      ),
    ])
    api.installPlatformBot.mockResolvedValue({
      bot: platformSlackBot({ status: "needs_reconnect" }),
      authorization_url: AUTHORIZE_URL,
    })
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Update needed")).toBeInTheDocument()
    expect(within(row).queryByText("Active")).toBeNull()
    expect(
      within(row).getByText("Missing: channels:read, groups:read")
    ).toBeInTheDocument()

    await userEvent.click(
      within(row).getByRole("button", { name: "Reconnect" })
    )
    expect(api.installPlatformBot.mock.calls[0][0].requestBody).toMatchObject({
      bot_id: "platform-1",
      agent_preset_id: undefined,
    })
    await waitFor(() => expect(goTo).toHaveBeenCalledWith(AUTHORIZE_URL))
  })

  it("asks a member without agent:update to find an editor for an update", async () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    servePlatform([
      platformSlackBot(
        {},
        { update_needed: true, missing_scopes: ["channels:read"] }
      ),
    ])
    renderChannelsPage()
    const row = await platformRow()
    expect(within(row).getByText("Update needed")).toBeInTheDocument()
    expect(
      within(row).getByText(/Ask someone who can edit agents to reconnect it\./)
    ).toBeInTheDocument()
    expect(within(row).queryByRole("button", { name: "Reconnect" })).toBeNull()
  })

  it("shows connected controls disabled without agent:update", async () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    servePlatform()
    renderChannelsPage()
    const row = await platformRow()
    expect(
      await within(row).findByRole("combobox", { name: "Default agent" })
    ).toBeDisabled()
    expect(
      within(row).getByRole("combobox", { name: "Channel" })
    ).toBeDisabled()
    expect(within(row).getByRole("button", { name: "Add" })).toBeDisabled()
    expect(
      within(row).getByRole("combobox", { name: "Who can use this bot" })
    ).toBeDisabled()
    expect(
      within(row).queryByRole("button", { name: "More actions" })
    ).toBeNull()
  })

  it("shows why the installs failed to load", async () => {
    api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
    api.listBots.mockRejectedValue(apiError(400, { detail: "Flaky" }))
    renderChannelsPage()
    expect(await screen.findByText("Flaky")).toBeInTheDocument()
  })

  it("shows why the platform apps failed to load", async () => {
    api.listPlatformApps.mockRejectedValue(apiError(400, { detail: "Flaky" }))
    renderChannelsPage()
    expect(await screen.findByText("Flaky")).toBeInTheDocument()
    expect(screen.queryByText(/is not available/)).toBeNull()
  })

  it("explains a Slack workspace another Tracecat workspace holds", async () => {
    window.history.replaceState(
      {},
      "",
      `${PAGE_PATH}?channel_bot_install=error&channel_bot_error=workspace_claimed&channel_bot_message=${encodeURIComponent(
        "Reworded refusal"
      )}`
    )
    servePlatform([])
    renderChannelsPage()
    const row = await platformRow()
    expect(
      within(row).getByText(
        "Another Tracecat workspace has that Slack workspace"
      )
    ).toBeInTheDocument()
    expect(toast).not.toHaveBeenCalled()
    expect(window.location.search).toBe("")
  })
})
