import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { registryActionsListRegistryActions } from "@/client"
import { useBuilderRegistryActions } from "@/lib/hooks"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    registryActionsListRegistryActions: jest.fn(),
  }
})

const mockRegistryActionsListRegistryActions =
  registryActionsListRegistryActions as jest.MockedFunction<
    typeof registryActionsListRegistryActions
  >

describe("useBuilderRegistryActions", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    })
    mockRegistryActionsListRegistryActions.mockResolvedValue([])
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }

  it("does not request locked actions by default", async () => {
    const { result } = renderHook(() => useBuilderRegistryActions(), {
      wrapper,
    })

    await waitFor(() => {
      expect(mockRegistryActionsListRegistryActions).toHaveBeenCalledTimes(1)
      expect(result.current.registryActions).toEqual([])
    })

    expect(mockRegistryActionsListRegistryActions).toHaveBeenCalledWith({})
  })

  it("requests locked actions when explicitly enabled", async () => {
    renderHook(() => useBuilderRegistryActions({ includeLocked: true }), {
      wrapper,
    })

    await waitFor(() => {
      expect(mockRegistryActionsListRegistryActions).toHaveBeenCalledWith({
        includeLocked: true,
      })
    })
  })
})
