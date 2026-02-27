/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { authDiscoverAuthMethod } from "@/client"
import { SignIn } from "@/components/auth/sign-in"
import { SignUp } from "@/components/auth/sign-up"

type MockAppInfo = {
  version: string
  public_app_url: string
  auth_allowed_types: string[]
  saml_enabled: boolean
  saml_enforced: boolean
  ee_multi_tenant: boolean
}

const mockRouterPush = jest.fn()
const mockLogin = jest.fn()
const mockLogout = jest.fn()
const mockRegister = jest.fn()

let mockUser: { email: string } | null = null
let mockAppInfo: MockAppInfo = {
  version: "test",
  public_app_url: "http://localhost:3000",
  auth_allowed_types: ["basic"],
  saml_enabled: false,
  saml_enforced: false,
  ee_multi_tenant: false,
}
let mockAppInfoIsLoading = false
let mockAppInfoError: Error | null = null

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}))

jest.mock("next/image", () => ({
  __esModule: true,
  default: (props: {
    src: string
    alt: string
    className?: string
  }): JSX.Element => <img alt={props.alt} className={props.className} />,
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: mockUser,
    userIsLoading: false,
  }),
  useAuthActions: () => ({
    login: mockLogin,
    logout: mockLogout,
    register: mockRegister,
  }),
}))

jest.mock("@/lib/hooks", () => ({
  useAppInfo: () => ({
    appInfo: mockAppInfo,
    appInfoIsLoading: mockAppInfoIsLoading,
    appInfoError: mockAppInfoError,
  }),
}))

jest.mock("@/client", () => {
  class MockApiError extends Error {
    body: { detail: unknown }

    constructor(detail: unknown = "") {
      super("ApiError")
      this.body = { detail }
    }
  }

  return {
    authDiscoverAuthMethod: jest.fn(),
    authOauthOidcDatabaseAuthorize: jest.fn(),
    ApiError: MockApiError,
  }
})

function setAuthTypes(authAllowedTypes: string[]): void {
  mockAppInfo = {
    ...mockAppInfo,
    auth_allowed_types: authAllowedTypes,
  }
}

function setMultiTenant(eeMultiTenant: boolean): void {
  mockAppInfo = {
    ...mockAppInfo,
    ee_multi_tenant: eeMultiTenant,
  }
}

describe("Auth UI matrix", () => {
  const mockDiscoverAuthMethod = authDiscoverAuthMethod as jest.MockedFunction<
    typeof authDiscoverAuthMethod
  >
  let consoleErrorSpy: jest.SpyInstance

  beforeEach(() => {
    jest.clearAllMocks()
    consoleErrorSpy = jest.spyOn(console, "error").mockImplementation(() => {})
    mockUser = null
    mockAppInfoIsLoading = false
    mockAppInfoError = null
    setAuthTypes(["basic"])
    setMultiTenant(false)
  })

  afterEach(() => {
    consoleErrorSpy.mockRestore()
  })

  it.each([
    { authTypes: ["basic"], expectsSignUp: true },
    { authTypes: ["basic", "oidc"], expectsSignUp: true },
    { authTypes: ["oidc"], expectsSignUp: false },
    { authTypes: ["google_oauth"], expectsSignUp: false },
    { authTypes: ["saml"], expectsSignUp: false },
  ])(
    "shows sign-up CTA on sign-in only when basic auth is enabled: $authTypes",
    ({ authTypes, expectsSignUp }) => {
      setAuthTypes(authTypes)
      render(<SignIn />)

      if (expectsSignUp) {
        expect(
          screen.getByRole("link", { name: "Sign up" })
        ).toBeInTheDocument()
      } else {
        expect(
          screen.queryByRole("link", { name: "Sign up" })
        ).not.toBeInTheDocument()
      }
    }
  )

  it("preserves org slug in sign-up link from sign-in", () => {
    render(
      <SignIn
        returnUrl="/invitations/accept?token=test-token"
        organizationSlug="acme"
      />
    )

    expect(screen.getByRole("link", { name: "Sign up" })).toHaveAttribute(
      "href",
      "/sign-up?returnUrl=%2Finvitations%2Faccept%3Ftoken%3Dtest-token&org=acme"
    )
  })

  it("does not render password login UI when basic auth is disabled, even if discovery suggests basic", async () => {
    setAuthTypes(["oidc"])
    mockDiscoverAuthMethod.mockResolvedValue({
      method: "basic",
      next_url: null,
      organization_slug: "acme",
    })

    render(<SignIn organizationSlug="acme" />)

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "user@example.com" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    await waitFor(() => {
      expect(mockDiscoverAuthMethod).toHaveBeenCalledWith({
        requestBody: {
          email: "user@example.com",
          org: "acme",
        },
      })
    })

    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Sign up" })
    ).not.toBeInTheDocument()
  })

  it.each([
    { authTypes: ["basic"], eeMultiTenant: false, expectsBasicSignUp: true },
    {
      authTypes: ["basic", "oidc"],
      eeMultiTenant: false,
      expectsBasicSignUp: true,
    },
    { authTypes: ["basic"], eeMultiTenant: true, expectsBasicSignUp: true },
    { authTypes: ["oidc"], eeMultiTenant: false, expectsBasicSignUp: false },
    { authTypes: ["saml"], eeMultiTenant: false, expectsBasicSignUp: false },
  ])(
    "gates sign-up UI by basic auth availability: $authTypes (multi-tenant=$eeMultiTenant)",
    ({ authTypes, eeMultiTenant, expectsBasicSignUp }) => {
      setAuthTypes(authTypes)
      setMultiTenant(eeMultiTenant)
      render(
        <SignUp
          returnUrl="/invitations/accept?token=test-token"
          organizationSlug="acme"
        />
      )

      if (expectsBasicSignUp) {
        expect(screen.getByLabelText("Email")).toBeInTheDocument()
        expect(screen.getByLabelText("Password")).toBeInTheDocument()
        expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
          "href",
          "/sign-in?returnUrl=%2Finvitations%2Faccept%3Ftoken%3Dtest-token&org=acme"
        )
      } else {
        expect(screen.getByText("Sign up unavailable")).toBeInTheDocument()
        expect(screen.queryByLabelText("Email")).not.toBeInTheDocument()
        expect(screen.queryByLabelText("Password")).not.toBeInTheDocument()
        expect(
          screen.getByRole("link", { name: "Back to sign in" })
        ).toHaveAttribute(
          "href",
          "/sign-in?returnUrl=%2Finvitations%2Faccept%3Ftoken%3Dtest-token&org=acme"
        )
      }
    }
  )
})
