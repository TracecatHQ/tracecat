import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  agentChannelBotsListBots,
  agentSessionsGetSessionChannelSync,
  agentSessionsListSessionChannelDestinations,
  agentSessionsUpdateSessionChannelSync,
} from "@/client"
import {
  DraftSlackSyncButton,
  SlackSyncButton,
} from "@/components/chat/slack-sync-button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { usePickedChannelDestination } from "@/hooks/use-session-channel-sync"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentChannelBotsListBots: jest.fn(),
  agentSessionsGetSessionChannelSync: jest.fn(),
  agentSessionsListSessionChannelDestinations: jest.fn(),
  agentSessionsUpdateSessionChannelSync: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => true, isLoading: false }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const getSync = jest.mocked(agentSessionsGetSessionChannelSync)
const listDestinations = jest.mocked(
  agentSessionsListSessionChannelDestinations
)
const updateSync = jest.mocked(agentSessionsUpdateSessionChannelSync)
const mockToast = jest.mocked(toast)

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>
  )
}

function renderButton({
  disabled = false,
  openPicker = false,
}: {
  disabled?: boolean
  openPicker?: boolean
} = {}) {
  return renderWithClient(
    <SlackSyncButton
      workspaceId="ws-1"
      sessionId="session-1"
      disabled={disabled}
      openPicker={openPicker}
    />
  )
}

beforeEach(() => {
  getSync.mockReset()
  listDestinations.mockReset()
  updateSync.mockReset()
  mockToast.mockReset()
})

test("hides the button when the actor cannot sync or the agent has no bot", async () => {
  getSync.mockResolvedValue({ available: false, enabled: false, linked: false })
  renderButton()
  await waitFor(() => expect(getSync).toHaveBeenCalled())
  expect(screen.queryByRole("button")).not.toBeInTheDocument()
})

test.each([
  [{ available: true, enabled: true, linked: true }, "Slack sync on", "on"],
  [{ available: true, enabled: false, linked: true }, "Slack sync off", "off"],
  [
    { available: true, enabled: false, linked: false },
    "Sync with Slack",
    "unlinked",
  ],
])("draws the %o state", async (sync, label, state) => {
  getSync.mockResolvedValue(sync)
  renderButton()
  const button = await screen.findByRole("button", { name: label })
  expect(button).toHaveAttribute("data-state-sync", state)
})

test("turning sync off sends it and says messages stop syncing", async () => {
  getSync.mockResolvedValue({ available: true, enabled: true, linked: true })
  updateSync.mockResolvedValue({
    available: true,
    enabled: false,
    linked: true,
  })
  renderButton()
  await userEvent.click(
    await screen.findByRole("button", { name: "Slack sync on" })
  )
  await waitFor(() =>
    expect(updateSync).toHaveBeenCalledWith({
      workspaceId: "ws-1",
      sessionId: "session-1",
      requestBody: { enabled: false, destination: null },
    })
  )
  expect(mockToast).toHaveBeenCalledWith({
    title: "Slack sync off",
    description: "Messages won't be synced to Slack.",
  })
  expect(
    await screen.findByRole("button", { name: "Slack sync off" })
  ).toBeInTheDocument()
})

test("turning sync back on says so", async () => {
  getSync.mockResolvedValue({ available: true, enabled: false, linked: true })
  updateSync.mockResolvedValue({ available: true, enabled: true, linked: true })
  renderButton()
  await userEvent.click(
    await screen.findByRole("button", { name: "Slack sync off" })
  )
  await waitFor(() =>
    expect(mockToast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Slack sync on" })
    )
  )
})

test("an unlinked session picks a channel and links to it", async () => {
  getSync.mockResolvedValue({ available: true, enabled: false, linked: false })
  listDestinations.mockResolvedValue({
    items: [
      { kind: "direct_message", id: null, name: "you" },
      { kind: "channel", id: "C1", name: "test", recent: true },
      { kind: "channel", id: "C2", name: "general" },
    ],
  })
  updateSync.mockResolvedValue({ available: true, enabled: true, linked: true })
  renderButton()

  await userEvent.click(
    await screen.findByRole("button", { name: "Sync with Slack" })
  )
  expect(await screen.findByPlaceholderText("Search in Slack…")).toHaveFocus()
  expect(await screen.findByText("Direct message (you)")).toBeInTheDocument()
  expect(screen.getByText("Recent")).toBeInTheDocument()
  expect(screen.getByText("general")).toBeInTheDocument()
  expect(updateSync).not.toHaveBeenCalled()

  await userEvent.click(screen.getByText("test"))

  await waitFor(() =>
    expect(updateSync).toHaveBeenCalledWith({
      workspaceId: "ws-1",
      sessionId: "session-1",
      requestBody: {
        enabled: true,
        destination: { kind: "channel", id: "C1" },
      },
    })
  )
  expect(mockToast).toHaveBeenCalledWith({
    title: "Slack sync on",
    description: "Linked to #test. New messages here post to its thread.",
  })
  expect(
    await screen.findByRole("button", { name: "Slack sync on" })
  ).toBeInTheDocument()
})

test("clicking the button again closes the picker", async () => {
  getSync.mockResolvedValue({ available: true, enabled: false, linked: false })
  listDestinations.mockResolvedValue({ items: [] })
  renderButton()
  const button = await screen.findByRole("button", { name: "Sync with Slack" })

  await userEvent.click(button)
  expect(await screen.findByPlaceholderText("Search in Slack…")).toBeVisible()
  await userEvent.click(button)

  await waitFor(() =>
    expect(
      screen.queryByPlaceholderText("Search in Slack…")
    ).not.toBeInTheDocument()
  )
  expect(updateSync).not.toHaveBeenCalled()
})

test("a disabled button never opens the picker", async () => {
  getSync.mockResolvedValue({ available: true, enabled: false, linked: false })
  renderButton({ disabled: true })
  const button = await screen.findByRole("button", { name: "Sync with Slack" })

  // The span trigger still takes the click a disabled button lets through.
  await userEvent.click(button.parentElement as HTMLElement)

  expect(
    screen.queryByPlaceholderText("Search in Slack…")
  ).not.toBeInTheDocument()
  expect(listDestinations).not.toHaveBeenCalled()
})

test("the picker selects with the keyboard", async () => {
  getSync.mockResolvedValue({ available: true, enabled: false, linked: false })
  listDestinations.mockResolvedValue({
    items: [
      { kind: "direct_message", id: null, name: "you" },
      { kind: "channel", id: "C2", name: "general" },
    ],
  })
  updateSync.mockResolvedValue({ available: true, enabled: true, linked: true })
  renderButton()

  await userEvent.click(
    await screen.findByRole("button", { name: "Sync with Slack" })
  )
  await screen.findByText("Direct message (you)")
  await userEvent.keyboard("{Enter}")

  await waitFor(() =>
    expect(updateSync).toHaveBeenCalledWith(
      expect.objectContaining({
        requestBody: {
          enabled: true,
          destination: { kind: "direct_message", id: null },
        },
      })
    )
  )
})

describe("a pick before the first message", () => {
  type Picked = ReturnType<typeof usePickedChannelDestination>
  const current: { picked?: Picked } = {}

  function Composer() {
    const picked = usePickedChannelDestination("session-1", true)
    current.picked = picked
    return (
      <SlackSyncButton
        workspaceId="ws-1"
        sessionId="session-1"
        picked={picked.picked}
        onPickedChange={picked.onPickedChange}
      />
    )
  }

  async function pickGeneral() {
    getSync.mockResolvedValue({
      available: true,
      enabled: false,
      linked: false,
    })
    listDestinations.mockResolvedValue({
      items: [{ kind: "channel", id: "C2", name: "general" }],
    })
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <Composer />
        </TooltipProvider>
      </QueryClientProvider>
    )
    await userEvent.click(
      await screen.findByRole("button", { name: "Sync with Slack" })
    )
    await userEvent.click(await screen.findByText("general"))
    return screen.findByRole("button", { name: "Slack sync on" })
  }

  test("shows on, links nothing, and the next send carries it once", async () => {
    const button = await pickGeneral()
    expect(button).toHaveAttribute("data-state-sync", "on")
    expect(updateSync).not.toHaveBeenCalled()

    let options: ReturnType<Picked["takeSendOptions"]>
    act(() => {
      options = current.picked?.takeSendOptions()
    })
    expect(options).toEqual({
      body: { channel_destination: { kind: "channel", id: "C2" } },
    })
    expect(
      await screen.findByRole("button", { name: "Sync with Slack" })
    ).toBeInTheDocument()
    act(() => {
      options = current.picked?.takeSendOptions()
    })
    expect(options).toBeUndefined()
  })

  test("turning it off before sending sends no destination", async () => {
    await userEvent.click(await pickGeneral())

    expect(
      await screen.findByRole("button", { name: "Sync with Slack" })
    ).toBeInTheDocument()
    let options: ReturnType<Picked["takeSendOptions"]>
    act(() => {
      options = current.picked?.takeSendOptions()
    })
    expect(options).toBeUndefined()
    expect(updateSync).not.toHaveBeenCalled()
  })
})

describe("a draft chat", () => {
  const listBots = jest.mocked(agentChannelBotsListBots)
  const page = (status: string) =>
    ({ items: [{ id: "bot-1", status }] }) as unknown as Awaited<
      ReturnType<typeof agentChannelBotsListBots>
    >

  beforeEach(() => listBots.mockReset())

  test("creates the session when the workspace has an active bot", async () => {
    listBots.mockResolvedValue(page("active"))
    const onCreateSession = jest.fn().mockResolvedValue(undefined)
    renderWithClient(
      <DraftSlackSyncButton
        workspaceId="ws-1"
        onCreateSession={onCreateSession}
      />
    )

    await userEvent.click(
      await screen.findByRole("button", { name: "Sync with Slack" })
    )
    expect(onCreateSession).toHaveBeenCalledTimes(1)
  })

  test("hides the button without an active bot", async () => {
    listBots.mockResolvedValue(page("paused"))
    renderWithClient(
      <DraftSlackSyncButton workspaceId="ws-1" onCreateSession={jest.fn()} />
    )
    await waitFor(() => expect(listBots).toHaveBeenCalled())
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  test("the session made to pick opens straight on the picker", async () => {
    getSync.mockResolvedValue({
      available: true,
      enabled: false,
      linked: false,
    })
    listDestinations.mockResolvedValue({ items: [] })
    renderButton({ openPicker: true })
    expect(await screen.findByPlaceholderText("Search in Slack…")).toBeVisible()
  })
})
