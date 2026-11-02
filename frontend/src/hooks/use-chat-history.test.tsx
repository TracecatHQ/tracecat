import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { ApiError, agentSessionsGetSessionVercel } from "@/client"
import { useGetChatVercel } from "@/hooks/use-chat"
import { DefaultQueryClientProvider } from "@/providers/query"

jest.mock("@/client", () => ({
  ApiError: jest.requireActual("@/client/core/ApiError").ApiError,
  agentSessionsGetSessionVercel: jest.fn(),
}))

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

function wrapper({ children }: { children: ReactNode }) {
  return <DefaultQueryClientProvider>{children}</DefaultQueryClientProvider>
}

describe("chat history retry policy", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.mocked(agentSessionsGetSessionVercel).mockReset()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it.each([
    { status: 404, retry: undefined, requests: 1 },
    { status: 422, retry: undefined, requests: 1 },
    { status: 500, retry: undefined, requests: 4 },
    { status: 500, retry: false, requests: 1 },
  ])(
    "makes $requests requests for HTTP $status with retry=$retry",
    async ({ status, retry, requests }) => {
      const error = new ApiError(
        { method: "GET", url: "/test-chat" },
        {
          url: "https://example.invalid/test-chat",
          ok: false,
          status,
          statusText: "Request failed",
          body: { detail: "Could not load chat" },
        },
        "Could not load chat"
      )
      const fetchHistory = jest.mocked(agentSessionsGetSessionVercel)
      fetchHistory.mockRejectedValue(error)
      const view = renderHook(
        () =>
          useGetChatVercel({
            chatId: "chat-1",
            workspaceId: "workspace-1",
            retry,
          }),
        { wrapper }
      )

      await act(async () => {
        await jest.advanceTimersByTimeAsync(10_000)
      })
      expect(fetchHistory).toHaveBeenCalledTimes(requests)
      expect(view.result.current.chatError).toBe(error)
      expect(view.result.current.chatFetching).toBe(false)
      view.unmount()
    }
  )
})
