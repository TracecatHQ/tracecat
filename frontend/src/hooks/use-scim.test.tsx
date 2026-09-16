import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { scimGetScimConnection } from "@/client"
import { useScimConnection } from "@/hooks/use-scim"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  scimGetScimConnection: jest.fn(),
  scimIssueScimToken: jest.fn(),
  scimRevokeScimToken: jest.fn(),
  scimListExternalGroups: jest.fn(),
  scimListScimMappings: jest.fn(),
  scimCreateScimMapping: jest.fn(),
  scimDeleteScimMapping: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

describe("useScimConnection", () => {
  beforeEach(() => {
    jest.mocked(scimGetScimConnection).mockReset()
  })

  it("treats a 404 as not configured rather than an error", async () => {
    jest
      .mocked(scimGetScimConnection)
      .mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }))

    const { result } = renderHook(() => useScimConnection(), {
      wrapper: createWrapper(createQueryClient()),
    })

    await waitFor(() => expect(result.current.connectionIsLoading).toBe(false))
    expect(result.current.connection).toBeNull()
    expect(result.current.connectionError).toBeNull()
  })

  it("surfaces a non-404 failure as a query error", async () => {
    jest
      .mocked(scimGetScimConnection)
      .mockRejectedValue(Object.assign(new Error("Forbidden"), { status: 403 }))

    const { result } = renderHook(() => useScimConnection(), {
      wrapper: createWrapper(createQueryClient()),
    })

    await waitFor(() => expect(result.current.connectionError).not.toBeNull())
    expect(result.current.connectionError?.status).toBe(403)
    expect(result.current.connection).toBeUndefined()
  })
})
