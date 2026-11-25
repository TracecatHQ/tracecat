import { render, screen } from "@testing-library/react"
import ChannelLinkPage from "@/app/channel-links/[token]/page"

let mockSearch = ""
let mockUser: { email: string } | null = null
const mockRouter = { replace: jest.fn() }
const mockMutate = jest.fn()

jest.mock("next/navigation", () => ({
  useParams: () => ({ token: "tok" }),
  useRouter: () => mockRouter,
  useSearchParams: () => new URLSearchParams(mockSearch),
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: mockUser, userIsLoading: false }),
}))
jest.mock("@/lib/query", () => ({
  useMutation: () => ({ error: null, mutate: mockMutate }),
}))

beforeEach(() => {
  mockRouter.replace.mockClear()
  mockMutate.mockClear()
  mockUser = null
})

test("a signed-out callback error shows instead of redirecting", () => {
  mockSearch = "result=error&error=signed_out"
  render(<ChannelLinkPage />)
  expect(
    screen.getByText("Your Tracecat session ended. Sign in again to retry.")
  ).toBeInTheDocument()
  expect(mockRouter.replace).not.toHaveBeenCalled()
})

test("only a spent or invalid link asks for a new one", () => {
  mockSearch = "result=error&error=wrong_account&provider=slack"
  const { unmount } = render(<ChannelLinkPage />)
  expect(
    screen.getByText("Open the link in Slack again to retry.")
  ).toBeInTheDocument()
  unmount()
  mockSearch = "result=error&error=invalid&provider=slack"
  render(<ChannelLinkPage />)
  expect(
    screen.getByText("Ask the agent again in Slack for a new link.")
  ).toBeInTheDocument()
})

test("a signed-out visit redirects to sign-in and back", () => {
  mockSearch = ""
  render(<ChannelLinkPage />)
  expect(mockRouter.replace).toHaveBeenCalledWith(
    "/sign-in?returnUrl=%2Fchannel-links%2Ftok"
  )
})

test("a signed-in visit starts provider sign-in once", () => {
  mockSearch = ""
  mockUser = { email: "user@example.com" }
  const { rerender } = render(<ChannelLinkPage />)
  rerender(<ChannelLinkPage />)
  expect(mockMutate).toHaveBeenCalledTimes(1)
  expect(mockRouter.replace).not.toHaveBeenCalled()
})

test("a linked callback names the provider and the Tracecat user", () => {
  mockSearch = "result=linked&provider=slack&tenant=T0000TEST"
  mockUser = { email: "user@example.com" }
  render(<ChannelLinkPage />)
  expect(screen.getByText("Slack account linked")).toBeInTheDocument()
  expect(
    screen.getByText("Your Slack account is now linked to Tracecat.")
  ).toBeInTheDocument()
  expect(screen.getByText("user@example.com")).toBeInTheDocument()
  expect(screen.getByRole("link", { name: "Back to Slack" })).toHaveAttribute(
    "href",
    "slack://open?team=T0000TEST"
  )
  expect(screen.queryByText("T0000TEST")).not.toBeInTheDocument()
  expect(mockMutate).not.toHaveBeenCalled()
})

test("a linked callback without a tenant only offers Tracecat", () => {
  mockSearch = "result=linked"
  mockUser = { email: "user@example.com" }
  render(<ChannelLinkPage />)
  expect(screen.getByText("Account linked")).toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: /Back to/ })
  ).not.toBeInTheDocument()
  expect(screen.getByRole("link", { name: "Go to Tracecat" })).toHaveAttribute(
    "href",
    "/workspaces"
  )
})

test("an error callback names the provider and shows the reason", () => {
  mockSearch =
    "result=error&error=wrong_account&provider=slack&tenant=T0000TEST"
  mockUser = { email: "user@example.com" }
  render(<ChannelLinkPage />)
  expect(
    screen.getByText("Couldn't link your Slack account")
  ).toBeInTheDocument()
  expect(screen.getByRole("alert")).toHaveTextContent(
    "You signed in to the provider as a different user than the one who asked to link."
  )
  expect(
    screen.getByRole("link", { name: "Back to Slack" })
  ).toBeInTheDocument()
})
