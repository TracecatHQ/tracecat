import { act, renderHook, waitFor } from "@testing-library/react"
import type { ChatStatus, UIMessageChunk } from "ai"
import type { ReactNode } from "react"
import {
  type AgentSessionsGetSessionVercelResponse,
  agentSessionsGetSessionVercel,
} from "@/client"
import {
  SubagentStreamContext,
  useSubagentTranscript,
} from "@/hooks/use-subagent-stream"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { SubagentStreamStore } from "@/lib/subagent-stream"

jest.mock("@/client", () => ({
  agentSessionsGetSessionVercel: jest.fn(),
}))

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))

if (typeof globalThis.structuredClone !== "function") {
  globalThis.structuredClone = ((value: unknown) =>
    JSON.parse(JSON.stringify(value))) as typeof structuredClone
}

const CHILD_ID = "0b5c6f7e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"
const LIVE_CHUNKS: UIMessageChunk[] = [
  { type: "start", messageId: CHILD_ID },
  { type: "text-start", id: "text-1" },
  { type: "text-delta", id: "text-1", delta: "Partial reply" },
]

function savedHistory(text?: string): AgentSessionsGetSessionVercelResponse {
  return {
    id: CHILD_ID,
    messages: text
      ? [
          {
            id: "saved-child-reply",
            role: "assistant",
            parts: [{ type: "text", text }],
          },
        ]
      : [],
  } as AgentSessionsGetSessionVercelResponse
}

function setup() {
  const store = new SubagentStreamStore()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 2 } },
  })
  let status: ChatStatus = "streaming"
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <SubagentStreamContext.Provider
          value={{ store, workspaceId: "workspace-1", status }}
        >
          {children}
        </SubagentStreamContext.Provider>
      </QueryClientProvider>
    )
  }
  const view = renderHook(
    () => useSubagentTranscript({ sessionId: CHILD_ID, finished: false }),
    { wrapper }
  )
  function replay() {
    LIVE_CHUNKS.forEach((chunk, index) =>
      store.ingest({ session_id: CHILD_ID, event_id: "event-1", index, chunk })
    )
  }
  return {
    ...view,
    store,
    queryClient,
    replay,
    setStatus(nextStatus: ChatStatus) {
      status = nextStatus
      view.rerender()
    },
    cleanup() {
      view.unmount()
      store.dispose()
      queryClient.clear()
    },
  }
}

describe("interrupted child transcript recovery", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.mocked(agentSessionsGetSessionVercel).mockReset()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it.each<ChatStatus>(["error", "ready"])(
    "recovers saved history after the parent becomes %s",
    async (status) => {
      const fetchHistory = jest.mocked(agentSessionsGetSessionVercel)
      fetchHistory.mockResolvedValue(savedHistory())
      const view = setup()
      act(view.replay)
      await waitFor(() => expect(view.result.current.isLive).toBe(true))
      expect(fetchHistory).not.toHaveBeenCalled()

      view.setStatus(status)
      await waitFor(() => expect(fetchHistory).toHaveBeenCalledTimes(1))
      expect(view.result.current.isLive).toBe(false)
      expect(view.result.current.messages[0]?.parts).toContainEqual(
        expect.objectContaining({ text: "Partial reply" })
      )
      expect(view.store.getMessage(CHILD_ID)).toBeDefined()

      fetchHistory.mockResolvedValue(savedHistory("Complete reply"))
      await act(async () => {
        await jest.advanceTimersByTimeAsync(3_000)
      })
      await waitFor(() =>
        expect(view.result.current.messages[0]?.parts).toContainEqual(
          expect.objectContaining({ text: "Complete reply" })
        )
      )
      expect(view.store.getMessage(CHILD_ID)).toBeUndefined()
      const requestCount = fetchHistory.mock.calls.length
      await act(async () => {
        await jest.advanceTimersByTimeAsync(9_000)
      })
      expect(fetchHistory).toHaveBeenCalledTimes(requestCount)

      // A later parent reconnect must not replace adopted history with replay.
      view.setStatus("streaming")
      act(view.replay)
      expect(view.result.current.messages[0]?.parts).toContainEqual(
        expect.objectContaining({ text: "Complete reply" })
      )
      expect(view.store.getMessage(CHILD_ID)).toBeUndefined()
      view.cleanup()
    }
  )

  it("preserves live replay state when reconnecting before history is saved", async () => {
    const fetchHistory = jest.mocked(agentSessionsGetSessionVercel)
    fetchHistory.mockRejectedValue(new Error("History unavailable"))
    const view = setup()
    act(view.replay)
    await waitFor(() => expect(view.result.current.isLive).toBe(true))

    view.setStatus("error")
    await waitFor(() => expect(view.result.current.isError).toBe(true))
    expect(view.result.current.messages[0]?.parts).toContainEqual(
      expect.objectContaining({ text: "Partial reply" })
    )

    view.setStatus("streaming")
    act(() => {
      view.replay()
      view.store.ingest({
        session_id: CHILD_ID,
        event_id: "event-2",
        index: 0,
        chunk: { type: "text-delta", id: "text-1", delta: " continued" },
      })
    })
    await waitFor(() =>
      expect(view.result.current.messages[0]?.parts).toContainEqual(
        expect.objectContaining({ text: "Partial reply continued" })
      )
    )
    expect(view.result.current.isLive).toBe(true)
    await act(async () => {
      await jest.advanceTimersByTimeAsync(9_000)
    })
    expect(fetchHistory).toHaveBeenCalledTimes(1)
    view.cleanup()
  })

  it.each(["empty", "failed"])(
    "bounds recovery of %s history and lets the user retry",
    async (response) => {
      const fetchHistory = jest.mocked(agentSessionsGetSessionVercel)
      if (response === "empty") {
        fetchHistory.mockResolvedValue(savedHistory())
      } else {
        fetchHistory.mockRejectedValue(new Error("History unavailable"))
      }
      const view = setup()
      act(view.replay)
      await waitFor(() => expect(view.result.current.isLive).toBe(true))
      view.setStatus("error")
      await waitFor(() => expect(fetchHistory).toHaveBeenCalledTimes(1))
      await act(async () => {
        await jest.advanceTimersByTimeAsync(60_001)
      })
      expect(view.result.current.isRecoveryPaused).toBe(true)
      expect(view.result.current.messages[0]?.parts).toContainEqual(
        expect.objectContaining({ text: "Partial reply" })
      )
      const requestCount = fetchHistory.mock.calls.length
      await act(async () => {
        await view.queryClient.invalidateQueries()
        await jest.advanceTimersByTimeAsync(120_000)
      })
      expect(fetchHistory).toHaveBeenCalledTimes(requestCount)

      fetchHistory.mockResolvedValue(savedHistory("Recovered reply"))
      act(view.result.current.retryRecovery)
      await waitFor(() =>
        expect(view.result.current.messages[0]?.parts).toContainEqual(
          expect.objectContaining({ text: "Recovered reply" })
        )
      )
      expect(view.result.current.isRecoveryPaused).toBe(false)
      expect(view.store.getMessage(CHILD_ID)).toBeUndefined()
      view.cleanup()
    }
  )

  it.each([403, 404])("stops immediately after HTTP %s", async (status) => {
    const fetchHistory = jest.mocked(agentSessionsGetSessionVercel)
    fetchHistory.mockRejectedValue({ status })
    const view = setup()
    view.setStatus("error")
    await waitFor(() => expect(view.result.current.isRecoveryPaused).toBe(true))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(12_000)
    })
    expect(fetchHistory).toHaveBeenCalledTimes(1)

    fetchHistory.mockResolvedValue(savedHistory("Recovered reply"))
    act(view.result.current.retryRecovery)
    await waitFor(() =>
      expect(view.result.current.messages[0]?.parts).toContainEqual(
        expect.objectContaining({ text: "Recovered reply" })
      )
    )
    expect(view.result.current.isRecoveryPaused).toBe(false)
    view.cleanup()
  })
})
