import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { useAuthActions } from "@/hooks/use-auth"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockPush = jest.fn()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))

jest.mock("@/client", () => ({
  authAuthDatabaseLogin: jest.fn().mockResolvedValue(undefined),
  authAuthDatabaseLogout: jest.fn().mockResolvedValue(undefined),
  authRegisterRegister: jest.fn(),
}))

jest.mock("@/lib/webauthn", () => ({
  getMfaStatus: jest.fn().mockResolvedValue(null),
  isMfaPending: jest.fn().mockReturnValue(false),
}))

jest.mock("@/lib/auth", () => ({
  getCurrentUser: jest.fn().mockResolvedValue(null),
  User: jest.fn(),
}))

function seedQueryClient(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(["auth"], { id: "previous-user" })
  queryClient.setQueryData(["workspaces"], [{ id: "previous-workspace" }])
  queryClient.setQueryData(["organization", "entitlements"], { all: true })
  return queryClient
}

function renderAuthActions(queryClient: QueryClient) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
  return renderHook(() => useAuthActions(), { wrapper: Wrapper })
}

describe("useAuthActions", () => {
  beforeEach(() => {
    mockPush.mockClear()
  })

  it("keeps mounted queries on logout so the current page doesn't refetch", async () => {
    const queryClient = seedQueryClient()
    const { result } = renderAuthActions(queryClient)

    await act(async () => {
      await result.current.logout()
    })

    expect(queryClient.getQueryData(["workspaces"])).toEqual([
      { id: "previous-workspace" },
    ])
    expect(queryClient.getQueryState(["auth"])?.isInvalidated).toBe(true)
    expect(mockPush).toHaveBeenCalledWith("/sign-in")
  })

  it("drops cached data from any earlier session on login", async () => {
    const queryClient = seedQueryClient()
    const { result } = renderAuthActions(queryClient)

    await act(async () => {
      await result.current.login({
        formData: { username: "next@example.com", password: "password" },
      })
    })

    expect(queryClient.getQueryData(["workspaces"])).toBeUndefined()
    expect(
      queryClient.getQueryData(["organization", "entitlements"])
    ).toBeUndefined()
    expect(queryClient.getQueryState(["auth"])?.isInvalidated).toBe(true)
  })
})
