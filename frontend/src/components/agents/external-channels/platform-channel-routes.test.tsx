import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentChannelRouteRead } from "@/client"
import {
  api,
  PRESET,
  platformSlackBot,
  serveBots,
  serveEntitlements,
  WORKSPACE_ID,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderPanel } from "@/components/agents/external-channels/channel-bot-test-render"

jest.mock("@/client")
jest.mock("@/components/agents/external-channels/slack-channel-panel", () => ({
  SlackChannelPanel: () => "Legacy Slack panel",
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const INSTALL_ID = "platform-1"

function route(channel: string, agentPresetId: string): AgentChannelRouteRead {
  return {
    bot_id: INSTALL_ID,
    provider_channel_id: channel,
    agent_preset_id: agentPresetId,
    created_at: "2026-11-15T00:00:00Z",
    updated_at: "2026-11-15T00:00:00Z",
  }
}

function servePlatform(routes: AgentChannelRouteRead[] = []): void {
  serveBots([platformSlackBot()])
  api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
  api.listRoutes.mockResolvedValue(routes)
}

/** Renders the Channels tab and waits for the install's route editor. */
async function editor(): Promise<HTMLElement> {
  renderPanel()
  const card = await screen.findByRole("group", { name: "Tracecat for Slack" })
  await within(card).findByText("Channels")
  return card
}

async function choose(
  card: HTMLElement,
  picker: string,
  agent: string
): Promise<void> {
  await userEvent.click(within(card).getByRole("combobox", { name: picker }))
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
  servePlatform()
  api.setRoute.mockResolvedValue(route("C1", PRESET.id))
  api.deleteRoute.mockResolvedValue(undefined)
  api.updateBot.mockResolvedValue(platformSlackBot())
})

describe("PlatformChannelRoutes", () => {
  it("routes a new channel to an agent", async () => {
    const card = await editor()
    expect(
      within(card).getByText("Every channel uses the default agent.")
    ).toBeInTheDocument()

    await userEvent.type(within(card).getByLabelText("Channel ID"), " C9 ")
    await choose(card, "Agent for the new route", "Phishing")
    await userEvent.click(within(card).getByRole("button", { name: "Add" }))

    expect(api.setRoute).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      requestBody: { provider_channel_id: "C9", agent_preset_id: "preset-2" },
    })
    await waitFor(() =>
      expect(within(card).getByLabelText("Channel ID")).toHaveValue("")
    )
  })

  it("refuses a channel that already has a route", async () => {
    servePlatform([route("C1", "preset-2")])
    const card = await editor()
    await within(card).findByText("C1")

    await userEvent.type(within(card).getByLabelText("Channel ID"), "C1")
    await choose(card, "Agent for the new route", "Triage")

    expect(within(card).getByRole("button", { name: "Add" })).toBeDisabled()
  })

  it("changes the agent a channel routes to", async () => {
    servePlatform([route("C1", "preset-2")])
    const card = await editor()
    const picker = await within(card).findByRole("combobox", {
      name: "Agent for C1",
    })
    expect(picker).toHaveTextContent("Phishing")

    await choose(card, "Agent for C1", "Triage")

    expect(api.setRoute).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      requestBody: { provider_channel_id: "C1", agent_preset_id: PRESET.id },
    })
  })

  it("removes a channel's route", async () => {
    servePlatform([route("C1", "preset-2")])
    const card = await editor()

    await userEvent.click(
      await within(card).findByRole("button", { name: "Remove C1" })
    )

    expect(api.deleteRoute).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      providerChannelId: "C1",
    })
  })

  it("swaps the install's default agent", async () => {
    const card = await editor()
    await waitFor(() =>
      expect(
        within(card).getByRole("combobox", { name: "Default agent" })
      ).toHaveTextContent("Triage")
    )

    await choose(card, "Default agent", "Phishing")

    expect(api.updateBot).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      requestBody: { agent_preset_id: "preset-2" },
    })
  })

  it("is hidden, with the card, without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    renderPanel()

    expect(await screen.findByText("Legacy Slack panel")).toBeInTheDocument()
    expect(
      screen.queryByRole("group", { name: "Tracecat for Slack" })
    ).toBeNull()
    expect(api.listPlatformApps).not.toHaveBeenCalled()
    expect(api.listRoutes).not.toHaveBeenCalled()
  })
})
