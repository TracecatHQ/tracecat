/**
 * @jest-environment jsdom
 */

import { render, waitFor } from "@testing-library/react"
import { AuthGuard } from "@/components/auth/auth-guard"

const mockRouterPush = jest.fn()

let mockUser: { isSuperuser: boolean } | null = null
let mockUserIsLoading = false
let mockUserScopes: { scopes: string[] } | undefined
let mockScopesLoading = false

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: mockUser,
    userIsLoading: mockUserIsLoading,
  }),
}))

jest.mock("@/lib/hooks", () => ({
  useUserScopes: () => ({
    userScopes: mockUserScopes,
    isLoading: mockScopesLoading,
  }),
}))

jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

describe("AuthGuard", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUser = null
    mockUserIsLoading = false
    mockUserScopes = undefined
    mockScopesLoading = false
  })

  it("redirects logged-out users to sign-in instead of the permission fallback", async () => {
    render(
      <AuthGuard requireAuth requireSuperuser redirectTo="/workspaces">
        Admin console
      </AuthGuard>
    )

    await waitFor(() => {
      expect(mockRouterPush).toHaveBeenCalledWith("/sign-in")
    })
    expect(mockRouterPush).not.toHaveBeenCalledWith("/workspaces")
  })

  it("keeps the permission fallback for authenticated users without access", async () => {
    mockUser = { isSuperuser: false }

    render(
      <AuthGuard requireAuth requireSuperuser redirectTo="/workspaces">
        Admin console
      </AuthGuard>
    )

    await waitFor(() => {
      expect(mockRouterPush).toHaveBeenCalledWith("/workspaces")
    })
  })
})
