import { render, screen } from "@testing-library/react"
import { RequesterShell } from "@/components/forms/requester/requester-shell"
import { useAuth } from "@/hooks/use-auth"
import { useFormsAccess } from "@/hooks/use-forms-access"

const replace = jest.fn()
const notFound = jest.fn(() => {
  throw new Error("NEXT_NOT_FOUND")
})

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  notFound: () => notFound(),
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: jest.fn(),
  useAuthActions: () => ({ logout: jest.fn() }),
}))

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: jest.fn(),
}))

const mockUseAuth = jest.mocked(useAuth)
const mockUseFormsAccess = jest.mocked(useFormsAccess)

const signedIn = {
  user: {
    email: "maya.okafor@example.com",
    firstName: "Maya",
    getDisplayName: () => "Maya Okafor",
  },
  userIsLoading: false,
  userError: null,
} as unknown as ReturnType<typeof useAuth>

function renderShell() {
  return render(
    <RequesterShell>
      <p>Form content</p>
    </RequesterShell>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUseAuth.mockReturnValue(signedIn)
  mockUseFormsAccess.mockReturnValue("enabled")
  window.history.pushState({}, "", "/forms/form-1?tab=submissions")
})

describe("RequesterShell", () => {
  it("sends a signed-out visitor to sign-in with a return URL", () => {
    mockUseAuth.mockReturnValue({
      user: null,
      userIsLoading: false,
      userError: null,
    })

    renderShell()

    expect(replace).toHaveBeenCalledWith(
      "/sign-in?returnUrl=%2Fforms%2Fform-1%3Ftab%3Dsubmissions"
    )
    expect(screen.queryByText("Form content")).not.toBeInTheDocument()
    // The gate is not mounted, so no authenticated request can fire.
    expect(mockUseFormsAccess).not.toHaveBeenCalled()
  })

  it("waits for the session before redirecting or reading access", () => {
    mockUseAuth.mockReturnValue({
      user: null,
      userIsLoading: true,
      userError: null,
    })

    renderShell()

    expect(replace).not.toHaveBeenCalled()
    expect(mockUseFormsAccess).not.toHaveBeenCalled()
    expect(screen.queryByText("Form content")).not.toBeInTheDocument()
  })

  it("renders nothing of the page while access is loading", () => {
    mockUseFormsAccess.mockReturnValue("loading")

    renderShell()

    expect(screen.queryByText("Form content")).not.toBeInTheDocument()
    expect(notFound).not.toHaveBeenCalled()
  })

  it("is a 404 when the feature flag is off", () => {
    mockUseFormsAccess.mockReturnValue("hidden")
    jest.spyOn(console, "error").mockImplementation(() => {})

    expect(renderShell).toThrow("NEXT_NOT_FOUND")
    expect(notFound).toHaveBeenCalled()
  })

  it("says Forms aren't available without the entitlement", () => {
    mockUseFormsAccess.mockReturnValue("locked")

    renderShell()

    expect(screen.getByText("Forms aren't available")).toBeInTheDocument()
    expect(screen.getByText("Contact your administrator.")).toBeInTheDocument()
    expect(screen.queryByText("Form content")).not.toBeInTheDocument()
    expect(replace).not.toHaveBeenCalled()
  })

  it("renders the page for a signed-in visitor when Forms is enabled", () => {
    renderShell()

    expect(screen.getByText("Form content")).toBeInTheDocument()
    expect(replace).not.toHaveBeenCalled()
    expect(notFound).not.toHaveBeenCalled()
  })
})
