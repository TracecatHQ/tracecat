import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { useBuiltInAgentCatalog } from "@/lib/hooks"

type MockFetchResponse = {
  ok: boolean
  status: number
  json: () => Promise<unknown>
  text: () => Promise<string>
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function createResponse(body: unknown, status = 200): MockFetchResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () =>
      typeof body === "string" ? body : JSON.stringify(body ?? null),
  }
}

describe("useBuiltInAgentCatalog", () => {
  const originalFetch = global.fetch

  afterEach(() => {
    global.fetch = originalFetch
    jest.clearAllMocks()
  })

  it("prefers the built-in catalog endpoint when it is available", async () => {
    const fetchMock = jest.fn().mockResolvedValue(
      createResponse({
        source_type: "platform_catalog",
        source_name: "Platform catalog",
        discovery_status: "ready",
        models: [
          {
            model_name: "gpt-5",
            model_provider: "openai",
            source_name: "Platform",
            source_id: null,
            enabled: false,
            credential_provider: "openai",
            credentials_configured: false,
            ready: false,
            enableable: false,
          },
        ],
      })
    )
    global.fetch = fetchMock as typeof fetch
    const queryClient = createQueryClient()

    const { result } = renderHook(() => useBuiltInAgentCatalog(), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => {
      expect(result.current.inventory).toBeDefined()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/catalog/platform?limit=100",
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    expect(result.current.inventory?.source_type).toBe("platform_catalog")
    expect(result.current.inventory?.models[0]?.model_name).toBe("gpt-5")
    expect(result.current.inventory?.models[0]?.enableable).toBe(false)
  })

  it("surfaces an error instead of falling back to removed legacy endpoints", async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValue(createResponse("missing", 404))
    global.fetch = fetchMock as typeof fetch
    const queryClient = createQueryClient()

    const { result } = renderHook(() => useBuiltInAgentCatalog(), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => {
      expect(result.current.error).toBeDefined()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent/catalog/platform?limit=100",
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    expect(result.current.inventory).toBeUndefined()
  })
})
