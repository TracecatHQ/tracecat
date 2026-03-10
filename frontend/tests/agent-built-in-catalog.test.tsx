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
        source_type: "builtin_catalog",
        source_name: "Built-in catalog",
        discovery_status: "ready",
        models: [
          {
            catalog_ref: "builtin_catalog:openai:abc123:gpt-5",
            model_name: "gpt-5",
            model_provider: "openai",
            runtime_provider: "openai",
            display_name: "GPT-5",
            source_type: "builtin_catalog",
            source_name: "Built-in catalog",
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
      "/api/agent/catalog/builtins?limit=100",
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    expect(result.current.inventory?.source_type).toBe("builtin_catalog")
    expect(result.current.inventory?.models[0]?.catalog_ref).toBe(
      "builtin_catalog:openai:abc123:gpt-5"
    )
    expect(result.current.inventory?.models[0]?.enableable).toBe(false)
  })

  it("falls back to the default-model inventory endpoint when the built-in endpoint is unavailable", async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(createResponse("missing", 404))
      .mockResolvedValueOnce(
        createResponse({
          source_type: "default_sidecar",
          source_name: "Default models",
          discovery_status: "ready",
          discovered_models: [
            {
              catalog_ref: "default_sidecar:default:def456:gpt-4o-mini",
              model_name: "gpt-4o-mini",
              model_provider: "openai",
              runtime_provider: "default_sidecar",
              display_name: "GPT-4o mini",
              source_type: "default_sidecar",
              source_name: "Default models",
              enabled: true,
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

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/agent/catalog/builtins?limit=100",
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    )
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/agent/default-models", {
      headers: {
        "Content-Type": "application/json",
      },
    })
    expect(result.current.inventory?.source_type).toBe("default_sidecar")
    expect(result.current.inventory?.models[0]?.catalog_ref).toBe(
      "default_sidecar:default:def456:gpt-4o-mini"
    )
    expect(result.current.inventory?.models[0]?.enableable).toBe(true)
  })
})
