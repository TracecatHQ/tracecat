import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  agentSessionsGetSessionChannelSync,
  agentSessionsUpdateSessionChannelSync,
} from "@/client"
import { ChannelSyncToggle } from "@/components/chat/channel-sync-toggle"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentSessionsGetSessionChannelSync: jest.fn(),
  agentSessionsUpdateSessionChannelSync: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const getSync = jest.mocked(agentSessionsGetSessionChannelSync)
const updateSync = jest.mocked(agentSessionsUpdateSessionChannelSync)

function renderToggle() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <ChannelSyncToggle workspaceId="ws-1" sessionId="session-1" />
      </TooltipProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  getSync.mockReset()
  updateSync.mockReset()
})

test("hides the switch when the session cannot sync", async () => {
  getSync.mockResolvedValue({ available: false, enabled: false })
  renderToggle()
  await waitFor(() => expect(getSync).toHaveBeenCalled())
  expect(screen.queryByRole("switch")).not.toBeInTheDocument()
})

test("turning sync off sends it and shows the new state", async () => {
  getSync.mockResolvedValue({ available: true, enabled: true })
  updateSync.mockResolvedValue({ available: true, enabled: false })
  renderToggle()
  const toggle = await screen.findByRole("switch", {
    name: "Sync with channel",
  })
  expect(toggle).toBeChecked()
  await userEvent.click(toggle)
  await waitFor(() =>
    expect(updateSync).toHaveBeenCalledWith({
      workspaceId: "ws-1",
      sessionId: "session-1",
      requestBody: { enabled: false },
    })
  )
  await waitFor(() => expect(toggle).not.toBeChecked())
})

test("opting a session in sends enabled", async () => {
  getSync.mockResolvedValue({ available: true, enabled: false })
  updateSync.mockResolvedValue({ available: true, enabled: true })
  renderToggle()
  await userEvent.click(
    await screen.findByRole("switch", { name: "Sync with channel" })
  )
  await waitFor(() =>
    expect(updateSync).toHaveBeenCalledWith(
      expect.objectContaining({ requestBody: { enabled: true } })
    )
  )
})
