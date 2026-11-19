import { render, screen } from "@testing-library/react"
import ChannelLinkPage from "@/app/channel-links/[token]/page"

let mockSearch = ""
const mockRouter = { replace: jest.fn() }

jest.mock("next/navigation", () => ({
  useParams: () => ({ token: "tok" }),
  useRouter: () => mockRouter,
  useSearchParams: () => new URLSearchParams(mockSearch),
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: null, userIsLoading: false }),
}))
jest.mock("@/lib/query", () => ({
  useQuery: () => ({ data: undefined, error: null }),
  useMutation: () => ({ error: null }),
}))

beforeEach(() => mockRouter.replace.mockClear())

test("a signed-out callback error shows instead of redirecting", () => {
  mockSearch = "result=error&error=signed_out"
  render(<ChannelLinkPage />)
  expect(
    screen.getByText(
      "Your Tracecat session ended. Open the link again to retry."
    )
  ).toBeInTheDocument()
  expect(mockRouter.replace).not.toHaveBeenCalled()
})

test("a signed-out visit redirects to sign-in and back", () => {
  mockSearch = ""
  render(<ChannelLinkPage />)
  expect(mockRouter.replace).toHaveBeenCalledWith(
    "/sign-in?returnUrl=%2Fchannel-links%2Ftok"
  )
})
