import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { settingsUpdateAgentOtelSettings } from "@/client"
import { toast } from "@/components/ui/use-toast"
import { useOrgAgentOtelSettings } from "@/hooks/use-org-agent-otel-settings"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  settingsGetAgentOtelSettings: jest.fn(() => new Promise(() => undefined)),
  settingsUpdateAgentOtelSettings: jest.fn(),
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

describe("useOrgAgentOtelSettings", () => {
  beforeEach(() => {
    jest.mocked(toast).mockClear()
    jest.mocked(settingsUpdateAgentOtelSettings).mockReset()
  })

  it("toasts a network failure that carries no response body", async () => {
    jest
      .mocked(settingsUpdateAgentOtelSettings)
      .mockRejectedValue(new Error("Network Error"))
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })

    const { result } = renderHook(() => useOrgAgentOtelSettings(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await expect(
        result.current.updateAgentOtelSettings({
          requestBody: { agent_otel_config: { enabled: false } },
        })
      ).rejects.toThrow("Network Error")
    })

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith({
        title: "Failed to update agent telemetry",
        description:
          "An error occurred while updating agent telemetry settings: Network Error",
      })
    )
  })
})
