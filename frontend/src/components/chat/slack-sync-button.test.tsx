import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  agentSessionsGetSessionChannelSync,
  agentSessionsListSessionChannelDestinations,
  agentSessionsUpdateSessionChannelSync,
} from "@/client"
import { SlackSyncButton } from "@/components/chat/slack-sync-button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentSessionsGetSessionChannelSync: jest.fn(),
  agentSessionsListSessionChannelDestinations: jest.fn(),
  agentSessionsUpdateSessionChannelSync: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const getSync = jest.mocked(agentSessionsGetSessionChannelSync)
const listDestinations = jest.mocked(
  agentSessionsListSessionChannelDestinations
)
const updateSync = jest.mocked(agentSessionsUpdateSessionChannelSync)
const mockToast = jest.mocked(toast)

function renderButton() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <SlackSyncButton workspaceId="ws-1" sessionId="session-1" />
      </TooltipProvider>
    </QueryClientProvider>
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
    title: "Slack sync disabled",
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
