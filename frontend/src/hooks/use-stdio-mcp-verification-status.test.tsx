import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"

import type { MCPVerificationStatusRead } from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  markStdioMcpVerificationStarted,
  useStdioMcpVerificationStatus,
} from "@/hooks/use-stdio-mcp-verification-status"

jest.mock("@/client", () => ({
  mcpIntegrationsGetMcpIntegrationVerificationStatus: jest.fn(
    () => new Promise(() => undefined)
  ),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const workspaceId = "workspace-test"
const mcpIntegrationId = "mcp-integration-test"
const queryKey = [
  "mcp-verification-status",
  workspaceId,
  mcpIntegrationId,
] as const

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

describe("useStdioMcpVerificationStatus", () => {
  beforeEach(() => {
    jest.mocked(toast).mockClear()
  })

  it("does not toast a saved failure loaded after a refresh", async () => {
    const refreshedIntegrationId = "mcp-integration-refreshed"
    const refreshedQueryKey = [
      "mcp-verification-status",
      workspaceId,
      refreshedIntegrationId,
    ] as const
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    queryClient.setQueryData<MCPVerificationStatusRead>(refreshedQueryKey, {
      status: "failed",
      error: "Invalid credentials",
    })

    const { result } = renderHook(
      () =>
        useStdioMcpVerificationStatus({
          workspaceId,
          pendingIntegrationIds: [refreshedIntegrationId],
        }),
      { wrapper: createWrapper(queryClient) }
    )

    await waitFor(() =>
      expect(result.current.get(refreshedIntegrationId)?.status).toBe("failed")
    )
    expect(toast).not.toHaveBeenCalled()
  })

  it("exposes live status and re-arms a failed verification on reconnect", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    markStdioMcpVerificationStarted(queryClient, workspaceId, mcpIntegrationId)

    const { result } = renderHook(
      () =>
        useStdioMcpVerificationStatus({
          workspaceId,
          pendingIntegrationIds: [mcpIntegrationId],
        }),
      { wrapper: createWrapper(queryClient) }
    )

    await waitFor(() =>
      expect(result.current.get(mcpIntegrationId)?.status).toBe("verifying")
    )

    act(() => {
      queryClient.setQueryData<MCPVerificationStatusRead>(queryKey, {
        status: "failed",
        error:
          "Downloading dependency\n│ FastMCP server │\nAuthentication failed: invalid credentials",
      })
    })
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(1))
    expect(result.current.get(mcpIntegrationId)?.status).toBe("failed")
    expect(toast).toHaveBeenLastCalledWith({
      title: "MCP server verification failed",
      description:
        "Authentication failed: invalid credentials — Open the failure badge for details.",
      variant: "destructive",
    })

    act(() => {
      markStdioMcpVerificationStarted(
        queryClient,
        workspaceId,
        mcpIntegrationId
      )
    })
    await waitFor(() =>
      expect(result.current.get(mcpIntegrationId)?.status).toBe("verifying")
    )

    act(() => {
      queryClient.setQueryData<MCPVerificationStatusRead>(queryKey, {
        status: "failed",
        error: "Invalid credentials",
      })
    })
    await waitFor(() => expect(toast).toHaveBeenCalledTimes(2))
  })
})
