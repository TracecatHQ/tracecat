import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  agentChannelUsersListMyChannelUsers,
  agentChannelUsersStartMyChannelLinkSignIn,
  agentChannelUsersUnlinkMyChannelUser,
} from "@/client"
import { LinkedAccountsSettings } from "@/components/settings/linked-accounts-settings"
import {
  SettingsModalProvider,
  useSettingsDeepLink,
  useSettingsModal,
} from "@/components/settings/settings-modal-context"
import { toast } from "@/components/ui/use-toast"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentChannelUsersListMyChannelUsers: jest.fn(),
  agentChannelUsersUnlinkMyChannelUser: jest.fn(async () => undefined),
  agentChannelUsersStartMyChannelLinkSignIn: jest.fn(),
}))

const mockHasEntitlement = jest.fn(() => true)
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: mockHasEntitlement }),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const listLinks = jest.mocked(agentChannelUsersListMyChannelUsers)
const unlinkLink = jest.mocked(agentChannelUsersUnlinkMyChannelUser)
const startSignIn = jest.mocked(agentChannelUsersStartMyChannelLinkSignIn)

const LINK = {
  id: "link-1",
  provider: "slack",
  provider_tenant_id: "T0000TEST",
  provider_user_id: "U0000TEST",
  user_id: "user-1",
  provider_email: "slack-user@example.com",
}

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsModalProvider>
        <LinkedAccountsSettings />
      </SettingsModalProvider>
    </QueryClientProvider>
  )
}

function DeepLinkedSettings({ signedIn }: { signedIn: boolean }) {
  useSettingsDeepLink(signedIn)
  const { open, activeSection } = useSettingsModal()
  return open && activeSection === "linked-accounts" ? (
    <LinkedAccountsSettings />
  ) : (
    <p>Settings closed</p>
  )
}

const deepLinkQueryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})

function deepLinkTree(signedIn: boolean) {
  return (
    <QueryClientProvider client={deepLinkQueryClient}>
      <SettingsModalProvider>
        <DeepLinkedSettings signedIn={signedIn} />
      </SettingsModalProvider>
    </QueryClientProvider>
  )
}

function renderDeepLink(signedIn: boolean) {
  deepLinkQueryClient.clear()
  return render(deepLinkTree(signedIn))
}

beforeEach(() => {
  listLinks.mockReset()
  unlinkLink.mockClear()
  startSignIn.mockReset()
  mockHasEntitlement.mockReturnValue(true)
  jest.mocked(toast).mockClear()
  window.history.replaceState({}, "", "/")
})

test("lists a link by provider and email, without provider ids", async () => {
  listLinks.mockResolvedValue({ items: [LINK] })
  renderSettings()
  expect(await screen.findByText("Slack")).toBeInTheDocument()
  expect(screen.getByText("slack-user@example.com")).toBeInTheDocument()
  expect(screen.queryByText(/T0000TEST|U0000TEST/)).not.toBeInTheDocument()
})

test("a link without an email reads as linked", async () => {
  listLinks.mockResolvedValue({ items: [{ ...LINK, provider_email: null }] })
  renderSettings()
  expect(await screen.findByText("Linked")).toBeInTheDocument()
})

test("unlink deletes the link only after confirming", async () => {
  listLinks.mockResolvedValue({ items: [LINK] })
  renderSettings()
  await userEvent.click(
    await screen.findByRole("button", { name: "Unlink Slack" })
  )
  expect(unlinkLink).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole("button", { name: "Unlink" }))
  await waitFor(() =>
    expect(unlinkLink).toHaveBeenCalledWith({ linkId: "link-1" })
  )
})

test("shows the empty state", async () => {
  listLinks.mockResolvedValue({ items: [] })
  renderSettings()
  expect(await screen.findByText("No linked accounts.")).toBeInTheDocument()
})

test("the link button starts a Slack sign-in with no link token", async () => {
  listLinks.mockResolvedValue({ items: [] })
  // Never resolves, so the test never navigates.
  startSignIn.mockReturnValue(new Promise(() => {}) as never)
  renderSettings()
  await userEvent.click(
    await screen.findByRole("button", { name: "Link Slack account" })
  )
  await waitFor(() => expect(startSignIn).toHaveBeenCalledWith())
})

test("hides the link button without the channels preview", async () => {
  mockHasEntitlement.mockReturnValue(false)
  listLinks.mockResolvedValue({ items: [] })
  renderSettings()
  expect(await screen.findByText("No linked accounts.")).toBeInTheDocument()
  expect(
    screen.queryByRole("button", { name: "Link Slack account" })
  ).not.toBeInTheDocument()
})

test("a Settings deep link opens Linked accounts and toasts the result", async () => {
  window.history.replaceState(
    {},
    "",
    "/workspaces?tab=a&settings=linked-accounts&result=error&error=no_bot"
  )
  listLinks.mockResolvedValue({ items: [] })
  renderDeepLink(true)
  expect(await screen.findByText("No linked accounts.")).toBeInTheDocument()
  expect(toast).toHaveBeenCalledTimes(1)
  expect(toast).toHaveBeenCalledWith(
    expect.objectContaining({
      description:
        "Your organization has no active Tracecat bot in that Slack workspace.",
      variant: "destructive",
    })
  )
  expect(window.location.search).toBe("?tab=a")
})

test("a Settings deep link waits until signed in", async () => {
  window.history.replaceState(
    {},
    "",
    "/workspaces?settings=linked-accounts&result=linked"
  )
  listLinks.mockResolvedValue({ items: [] })
  const { rerender } = renderDeepLink(false)
  expect(screen.getByText("Settings closed")).toBeInTheDocument()
  expect(toast).not.toHaveBeenCalled()

  rerender(deepLinkTree(true))
  expect(await screen.findByText("No linked accounts.")).toBeInTheDocument()
  expect(toast).toHaveBeenCalledTimes(1)
  expect(toast).toHaveBeenCalledWith({ title: "Slack account linked" })
})
