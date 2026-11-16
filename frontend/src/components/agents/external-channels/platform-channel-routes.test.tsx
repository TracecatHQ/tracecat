import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentChannelRouteRead, ChannelBotChannel } from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import {
  api,
  apiError,
  channelPage,
  PRESET,
  platformSlackBot,
  routePage,
  serveBots,
  serveEntitlements,
  WORKSPACE_ID,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderChannelsPage } from "@/components/agents/external-channels/channel-bot-test-render"
import { toast } from "@/components/ui/use-toast"

const mockUseScopeCheck = jest.fn<boolean, [string]>(() => true)

jest.mock("@/client")
jest.mock("@/components/ui/use-toast", () => ({
  ...jest.requireActual("@/components/ui/use-toast"),
  toast: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

const INSTALL_ID = "platform-1"

const CHANNELS: ChannelBotChannel[] = [
  { id: "C1", name: "alerts", is_private: false, is_member: true },
  { id: "C2", name: "phishing-reports", is_private: false, is_member: false },
  { id: "G1", name: "soc", is_private: true, is_member: false },
]

function route(channel: string, agentPresetId: string): AgentChannelRouteRead {
  return {
    bot_id: INSTALL_ID,
    provider_channel_id: channel,
    agent_preset_id: agentPresetId,
    created_at: "2026-11-15T00:00:00Z",
    updated_at: "2026-11-15T00:00:00Z",
  }
}

function servePlatform(
  routes: AgentChannelRouteRead[] = [],
  channels: ChannelBotChannel[] = CHANNELS
): void {
  serveBots([platformSlackBot()])
  api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
  api.listRoutes.mockResolvedValue(routePage(routes))
  api.listRoutableChannels.mockResolvedValue(channelPage(channels))
}

/** Renders the Channels page and waits for the install's route editor. */
async function editor(): Promise<HTMLElement> {
  renderChannelsPage()
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

/** Opens the channel picker once the list has loaded. */
async function openPicker(card: HTMLElement): Promise<void> {
  const picker = within(card).getByRole("combobox", { name: "Channel" })
  await waitFor(() => expect(picker).toBeEnabled())
  await userEvent.click(picker)
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
  servePlatform()
  api.addRoute.mockResolvedValue(route("C9", "preset-2"))
  api.setRoute.mockResolvedValue(route("C1", PRESET.id))
  api.deleteRoute.mockResolvedValue(undefined)
  api.updateBot.mockResolvedValue(platformSlackBot())
})

describe("PlatformChannelRoutes", () => {
  it("routes a channel picked from the list", async () => {
    const card = await editor()
    expect(
      within(card).getByText("Every channel uses the default agent.")
    ).toBeInTheDocument()

    await openPicker(card)
    await userEvent.click(screen.getByRole("option", { name: /alerts/ }))
    await choose(card, "Agent for the new route", "Phishing")
    await userEvent.click(within(card).getByRole("button", { name: "Add" }))

    expect(api.listRoutableChannels).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      cursor: null,
    })
    expect(api.addRoute).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      requestBody: { provider_channel_id: "C1", agent_preset_id: "preset-2" },
    })
    expect(api.setRoute).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(
        within(card).getByRole("combobox", { name: "Channel" })
      ).toHaveTextContent("Pick a channel")
    )
  })

  it("filters the channels by name", async () => {
    const card = await editor()
    await openPicker(card)
    expect(screen.getAllByRole("option")).toHaveLength(3)

    await userEvent.type(
      screen.getByPlaceholderText("Search channels or paste a channel ID"),
      "phish"
    )

    expect(screen.getAllByRole("option")).toHaveLength(1)
    expect(
      screen.getByRole("option", { name: /phishing-reports/ })
    ).toBeInTheDocument()
  })

  it("loads more channels, even while searching the loaded ones", async () => {
    const [alerts, phishing, soc] = CHANNELS
    api.listRoutableChannels
      .mockResolvedValueOnce(channelPage([alerts], "next"))
      .mockResolvedValueOnce(channelPage([phishing, soc]))
    const card = await editor()
    await openPicker(card)

    await userEvent.type(
      screen.getByPlaceholderText("Search channels or paste a channel ID"),
      "soc"
    )
    expect(screen.queryByRole("option", { name: /soc/ })).toBeNull()
    await userEvent.click(
      screen.getByRole("option", { name: "Load more channels" })
    )

    expect(
      await screen.findByRole("option", { name: /soc/ })
    ).toBeInTheDocument()
    expect(api.listRoutableChannels).toHaveBeenLastCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      cursor: "next",
    })
    expect(
      screen.queryByRole("option", { name: "Load more channels" })
    ).toBeNull()
  })

  it("keeps the picker when a later channel page fails", async () => {
    api.listRoutableChannels
      .mockResolvedValueOnce(channelPage([CHANNELS[0]], "next"))
      .mockRejectedValueOnce(apiError(400, { detail: "Flaky" }))
    const card = await editor()
    await openPicker(card)

    await userEvent.click(
      screen.getByRole("option", { name: "Load more channels" })
    )

    expect(
      await screen.findByRole("option", {
        name: "Could not load more channels. Try again",
      })
    ).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /alerts/ })).toBeInTheDocument()
    expect(
      within(card).getByRole("combobox", { name: "Channel" })
    ).toBeInTheDocument()
  })

  it("holds the invite hint for an unlisted channel until every page loads", async () => {
    servePlatform([route("C9", PRESET.id)])
    api.listRoutableChannels.mockResolvedValue(
      channelPage([CHANNELS[0]], "next")
    )
    const card = await editor()

    const list = await within(card).findByRole("list", { name: "Channels" })
    expect(await within(list).findByText("C9")).toBeInTheDocument()
    expect(within(card).queryByText(/Invite @Tracecat/)).toBeNull()
  })

  it("marks private channels and channels the bot is not in", async () => {
    const card = await editor()
    await openPicker(card)

    const soc = screen.getByRole("option", { name: /soc/ })
    expect(within(soc).getByLabelText("Private channel")).toBeInTheDocument()
    expect(soc).toHaveTextContent("needs invite")
    const phishing = screen.getByRole("option", { name: /phishing-reports/ })
    expect(within(phishing).queryByLabelText("Private channel")).toBeNull()
    expect(phishing).toHaveTextContent("joins on add")
    const alerts = screen.getByRole("option", { name: /alerts/ })
    expect(alerts).not.toHaveTextContent(/joins on add|needs invite/)
  })

  it("leaves routed channels out of the list", async () => {
    servePlatform([route("C1", "preset-2")])
    const card = await editor()
    await within(card).findByRole("combobox", { name: "Agent for #alerts" })
    await openPicker(card)

    expect(screen.queryByRole("option", { name: /alerts/ })).toBeNull()
    expect(screen.getAllByRole("option")).toHaveLength(2)
  })

  it("shows the invite step after routing a private channel", async () => {
    let routes: AgentChannelRouteRead[] = []
    api.listRoutes.mockImplementation(
      () => new CancelablePromise((resolve) => resolve(routePage(routes)))
    )
    api.addRoute.mockImplementation(({ requestBody }) => {
      const added = route(
        requestBody.provider_channel_id,
        requestBody.agent_preset_id
      )
      routes = [added]
      return new CancelablePromise((resolve) => resolve(added))
    })
    const card = await editor()

    await openPicker(card)
    await userEvent.click(screen.getByRole("option", { name: /soc/ }))
    await choose(card, "Agent for the new route", "Phishing")
    await userEvent.click(within(card).getByRole("button", { name: "Add" }))

    expect(
      await within(card).findByText("Invite @Tracecat in soc to finish")
    ).toBeInTheDocument()
    expect(
      within(card).getByRole("button", { name: "Copy /invite @Tracecat" })
    ).toBeInTheDocument()
  })

  it("names routed channels it can list, and shows the ID otherwise", async () => {
    servePlatform([route("C1", "preset-2"), route("C9", PRESET.id)])
    const card = await editor()

    const list = await within(card).findByRole("list", { name: "Channels" })
    expect(await within(list).findByText("alerts")).toBeInTheDocument()
    expect(within(list).getByText("C9")).toBeInTheDocument()
    expect(
      within(card).getByRole("button", { name: "Remove #alerts" })
    ).toBeInTheDocument()
    expect(
      within(card).getByRole("button", { name: "Remove C9" })
    ).toBeInTheDocument()
    // Only the unlisted channel, likely private without the bot, needs an invite.
    expect(within(card).getAllByText(/Invite @Tracecat/)).toHaveLength(1)
    expect(
      within(card).getByText("Invite @Tracecat in this channel to finish")
    ).toBeInTheDocument()
  })

  it("names each channel after its team when the tenant spans several", async () => {
    const grid: ChannelBotChannel[] = [
      { ...CHANNELS[0], team_id: "T1", team_name: "SecOps" },
      { ...CHANNELS[2], team_id: "T2", team_name: "IT" },
    ]
    servePlatform([route("C1", "preset-2")], grid)
    const card = await editor()

    const list = await within(card).findByRole("list", { name: "Channels" })
    expect(await within(list).findByText("SecOps › alerts")).toBeInTheDocument()
    expect(
      within(card).getByRole("button", { name: "Remove SecOps › #alerts" })
    ).toBeInTheDocument()
    await openPicker(card)
    expect(screen.getByRole("option", { name: /IT › soc/ })).toBeInTheDocument()
  })

  it("routes a pasted channel ID the list does not have", async () => {
    const card = await editor()
    await openPicker(card)
    const search = screen.getByPlaceholderText(
      "Search channels or paste a channel ID"
    )
    await userEvent.type(search, "C1")
    expect(screen.queryByRole("option", { name: /Use channel ID/ })).toBeNull()

    await userEvent.clear(search)
    await userEvent.type(search, "c0123abcd")
    await userEvent.click(
      screen.getByRole("option", { name: "Use channel ID C0123ABCD" })
    )
    expect(
      within(card).getByRole("combobox", { name: "Channel" })
    ).toHaveTextContent("C0123ABCD")
    await choose(card, "Agent for the new route", "Phishing")
    await userEvent.click(within(card).getByRole("button", { name: "Add" }))

    expect(api.addRoute).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      requestBody: {
        provider_channel_id: "C0123ABCD",
        agent_preset_id: "preset-2",
      },
    })
  })

  it("marks a routed private channel with a lock", async () => {
    servePlatform([route("G1", "preset-2")])
    const card = await editor()

    const list = await within(card).findByRole("list", { name: "Channels" })
    expect(await within(list).findByText("soc")).toBeInTheDocument()
    expect(within(list).getByLabelText("Private channel")).toBeInTheDocument()
  })

  it.each([
    [501, "Slack channels can't be listed here."],
    [400, "Could not load channels."],
  ])(
    "falls back to the channel ID on a %i from the listing",
    async (status, note) => {
      api.listRoutableChannels.mockRejectedValue(
        apiError(status, { detail: "no" })
      )
      const card = await editor()
      expect(
        await within(card).findByText(note, { exact: false })
      ).toBeVisible()
      expect(
        within(card).queryByRole("combobox", { name: "Channel" })
      ).toBeNull()

      await userEvent.type(within(card).getByLabelText("Channel ID"), " c9 ")
      await choose(card, "Agent for the new route", "Phishing")
      await userEvent.click(within(card).getByRole("button", { name: "Add" }))

      expect(api.addRoute).toHaveBeenCalledWith({
        workspaceId: WORKSPACE_ID,
        botId: INSTALL_ID,
        requestBody: { provider_channel_id: "C9", agent_preset_id: "preset-2" },
      })
      await waitFor(() =>
        expect(within(card).getByLabelText("Channel ID")).toHaveValue("")
      )
    }
  )

  it("refuses a typed channel that already has a route", async () => {
    servePlatform([route("C1", "preset-2")])
    api.listRoutableChannels.mockRejectedValue(apiError(501, { detail: "no" }))
    const card = await editor()
    await within(card).findByText("C1")

    await userEvent.type(await within(card).findByLabelText("Channel ID"), "C1")
    await choose(card, "Agent for the new route", "Triage")

    expect(within(card).getByRole("button", { name: "Add" })).toBeDisabled()
  })

  it("changes the agent a channel routes to", async () => {
    servePlatform([route("C1", "preset-2")])
    const card = await editor()
    const picker = await within(card).findByRole("combobox", {
      name: "Agent for #alerts",
    })
    expect(picker).toHaveTextContent("Phishing")

    await choose(card, "Agent for #alerts", "Triage")

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
      await within(card).findByRole("button", { name: "Remove #alerts" })
    )

    expect(api.deleteRoute).toHaveBeenCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      providerChannelId: "C1",
    })
  })

  it("keeps the new-route form when a listed channel changes agent", async () => {
    servePlatform([route("C1", "preset-2")])
    const card = await editor()
    await within(card).findByRole("combobox", { name: "Agent for #alerts" })
    await openPicker(card)
    await userEvent.click(
      screen.getByRole("option", { name: /phishing-reports/ })
    )

    await choose(card, "Agent for #alerts", "Triage")

    await waitFor(() => expect(api.setRoute).toHaveBeenCalled())
    expect(
      within(card).getByRole("combobox", { name: "Channel" })
    ).toHaveTextContent("phishing-reports")
  })

  it("shows why the channels failed to load and still adds one", async () => {
    api.listRoutes.mockRejectedValue(apiError(400, { detail: "Flaky" }))
    const card = await editor()
    expect(await within(card).findByText("Flaky")).toBeInTheDocument()
    await openPicker(card)
  })

  it("says a channel is already routed when the API refuses it", async () => {
    api.addRoute.mockRejectedValue(apiError(409, { detail: "C2 has a route" }))
    const card = await editor()

    await openPicker(card)
    await userEvent.click(
      screen.getByRole("option", { name: /phishing-reports/ })
    )
    await choose(card, "Agent for the new route", "Phishing")
    await userEvent.click(within(card).getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          description:
            "This channel is already routed. Change its agent in the list instead.",
        })
      )
    )
    expect(
      within(card).getByRole("combobox", { name: "Channel" })
    ).toHaveTextContent("phishing-reports")
  })

  it("keeps the loaded channels when a later page fails", async () => {
    api.listRoutes
      .mockResolvedValueOnce(routePage([route("C1", "preset-2")], "next"))
      .mockRejectedValueOnce(apiError(400, { detail: "Flaky" }))
    const card = await editor()
    await within(card).findByText("alerts")

    await userEvent.click(
      within(card).getByRole("button", { name: "Show more" })
    )

    expect(await within(card).findByText("Flaky")).toBeInTheDocument()
    expect(within(card).getByText("alerts")).toBeInTheDocument()
    expect(
      within(card).getByRole("button", { name: "Show more" })
    ).toBeEnabled()
  })

  it("shows more channels a page at a time, still editable meanwhile", async () => {
    let finish: (page: ReturnType<typeof routePage>) => void = () => undefined
    api.listRoutes
      .mockResolvedValueOnce(routePage([route("C1", "preset-2")], "next"))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve
        }) as never
      )
    const card = await editor()
    await within(card).findByText("alerts")

    await userEvent.click(
      within(card).getByRole("button", { name: "Show more" })
    )

    expect(
      await within(card).findByRole("button", { name: "Loading..." })
    ).toBeDisabled()
    expect(
      within(card).getByRole("combobox", { name: "Channel" })
    ).toBeEnabled()
    expect(
      within(card).getByRole("combobox", { name: "Default agent" })
    ).toBeEnabled()
    expect(api.listRoutes).toHaveBeenLastCalledWith({
      workspaceId: WORKSPACE_ID,
      botId: INSTALL_ID,
      cursor: "next",
    })

    finish(routePage([route("C2", PRESET.id)]))
    expect(
      await within(card).findByText("phishing-reports")
    ).toBeInTheDocument()
    expect(within(card).getByText("alerts")).toBeInTheDocument()
    expect(within(card).queryByRole("button", { name: "Show more" })).toBeNull()
  })

  it("is read-only without agent:update", async () => {
    mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
    servePlatform([route("C1", "preset-2")])
    const card = await editor()
    await within(card).findByText("alerts")

    for (const name of [
      "Default agent",
      "Agent for #alerts",
      "Agent for the new route",
      "Channel",
    ]) {
      expect(within(card).getByRole("combobox", { name })).toBeDisabled()
    }
    expect(within(card).getByRole("button", { name: "Add" })).toBeDisabled()
    expect(
      within(card).getByRole("button", { name: "Remove #alerts" })
    ).toBeDisabled()
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
    renderChannelsPage()

    expect(
      await screen.findByText(/Tracecat for Slack is not available/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("group", { name: "Tracecat for Slack" })
    ).toBeNull()
    expect(api.listPlatformApps).not.toHaveBeenCalled()
    expect(api.listRoutes).not.toHaveBeenCalled()
    expect(api.listRoutableChannels).not.toHaveBeenCalled()
  })
})
