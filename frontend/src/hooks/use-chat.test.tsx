import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import type { ChatStatus, UIMessage } from "ai"
import type { ReactNode } from "react"
import {
  decideServerTranscriptAdoption,
  useAdoptServerTranscript,
} from "@/hooks/use-chat"

function textMessage(
  id: string,
  role: UIMessage["role"],
  text: string
): UIMessage {
  return { id, role, parts: [{ type: "text", text }] }
}

function approvalMessage(id: string): UIMessage {
  return {
    id,
    role: "assistant",
    parts: [
      {
        type: "data-approval-request",
        data: [],
      } as UIMessage["parts"][number],
    ],
  }
}

function toolMessage(id: string, toolCallId: string): UIMessage {
  return {
    id,
    role: "assistant",
    parts: [
      {
        type: "dynamic-tool",
        toolName: "lookup",
        toolCallId,
        state: "input-available",
        input: {},
      },
    ],
  }
}

function createWrapper(queryClient: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

async function advanceTimersBy(ms: number): Promise<void> {
  await act(async () => {
    jest.advanceTimersByTime(ms)
    await Promise.resolve()
  })
}

describe("useAdoptServerTranscript", () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it("retains final assistant text missing from an equal-length snapshot and schedules a refetch", async () => {
    const queryClient = new QueryClient()
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Question"),
      textMessage("live-assistant", "assistant", "Final streamed answer"),
    ]
    const serverMessages = [
      textMessage("server-user", "user", "Question"),
      textMessage("server-assistant", "assistant", "Previous answer"),
    ]

    const { unmount } = renderHook(
      () =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status: "ready",
          serverMessages,
          liveMessages,
          setMessages,
        }),
      { wrapper: createWrapper(queryClient) }
    )

    expect(setMessages).not.toHaveBeenCalled()
    expect(jest.getTimerCount()).toBe(3)

    await advanceTimersBy(1_000)

    expect(invalidateQueries).toHaveBeenCalledTimes(1)
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["chat", "chat-1", "workspace-1", "vercel"],
    })
    expect(setMessages).not.toHaveBeenCalled()
    unmount()
  })

  it("adopts a later split-row snapshot and cancels remaining retries", async () => {
    const queryClient = new QueryClient()
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Question"),
      textMessage("live-assistant", "assistant", "Final streamed answer"),
    ]
    const staleMessages = [
      textMessage("stale-user", "user", "Question"),
      textMessage("stale-assistant", "assistant", "Previous answer"),
    ]
    const caughtUpMessages = [
      textMessage("server-user", "user", "Question"),
      textMessage("server-assistant-1", "assistant", "Final streamed "),
      textMessage("server-assistant-2", "assistant", "answer"),
    ]

    const { rerender, unmount } = renderHook(
      ({ serverMessages }: { serverMessages: UIMessage[] }) =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status: "ready",
          serverMessages,
          liveMessages,
          setMessages,
        }),
      {
        initialProps: { serverMessages: staleMessages },
        wrapper: createWrapper(queryClient),
      }
    )

    await advanceTimersBy(1_000)
    expect(invalidateQueries).toHaveBeenCalledTimes(1)

    rerender({ serverMessages: caughtUpMessages })

    expect(setMessages).toHaveBeenCalledWith(caughtUpMessages)
    expect(jest.getTimerCount()).toBe(0)

    await advanceTimersBy(8_000)
    expect(invalidateQueries).toHaveBeenCalledTimes(1)
    unmount()
  })

  it("adopts a resolved-approval snapshot after its approval card is dropped", () => {
    const queryClient = new QueryClient()
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Run the action"),
      textMessage("live-assistant", "assistant", "Approval required"),
      approvalMessage("live-approval"),
    ]
    const serverMessages = [
      textMessage("server-user", "user", "Run the action"),
      textMessage("server-assistant", "assistant", "Approval required"),
    ]

    const { unmount } = renderHook(
      () =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status: "ready",
          serverMessages,
          liveMessages,
          setMessages,
        }),
      { wrapper: createWrapper(queryClient) }
    )

    expect(setMessages).toHaveBeenCalledWith(serverMessages)
    expect(jest.getTimerCount()).toBe(0)
    unmount()
  })

  it("adopts a count-covered snapshot after the bounded retries are exhausted", async () => {
    const queryClient = new QueryClient()
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Question"),
      textMessage("live-assistant", "assistant", "Stream-only wording"),
    ]
    const serverMessages = [
      textMessage("server-user", "user", "Question"),
      textMessage("server-assistant", "assistant", "Canonical wording"),
    ]

    const { unmount } = renderHook(
      () =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status: "ready",
          serverMessages,
          liveMessages,
          setMessages,
        }),
      { wrapper: createWrapper(queryClient) }
    )

    await advanceTimersBy(1_000)
    expect(setMessages).not.toHaveBeenCalled()
    await advanceTimersBy(2_000)
    expect(setMessages).not.toHaveBeenCalled()
    await advanceTimersBy(5_000)

    expect(invalidateQueries).toHaveBeenCalledTimes(3)
    expect(setMessages).toHaveBeenCalledWith(serverMessages)
    expect(jest.getTimerCount()).toBe(0)
    unmount()
  })

  it("uses only the count guard for a textless tool-only final assistant message", () => {
    const liveMessages = [
      textMessage("live-user", "user", "Look this up"),
      toolMessage("live-tool", "live-call"),
    ]
    const countCoveredServerMessages = [
      textMessage("server-user", "user", "Look this up"),
      toolMessage("server-tool", "server-call"),
    ]

    expect(
      decideServerTranscriptAdoption({
        serverMessages: countCoveredServerMessages,
        liveMessages,
      })
    ).toBe("adopt")
    expect(
      decideServerTranscriptAdoption({
        serverMessages: countCoveredServerMessages.slice(0, 1),
        liveMessages,
      })
    ).toBe("reject-count")
  })

  it("rejects a stale snapshot whose earlier turn repeats the final answer text", () => {
    const liveMessages = [
      textMessage("live-user-1", "user", "Question"),
      textMessage("live-assistant-1", "assistant", "Same answer"),
      textMessage("live-user-2", "user", "Question again"),
      textMessage("live-assistant-2", "assistant", "Same answer"),
    ]
    // Stale finalize-race snapshot: the earlier turn already contains the
    // identical text and DB row segmentation makes the snapshot as long as the
    // live transcript, but the final turn's rows are still hidden. Coverage
    // must be scoped to the final turn or this would be adopted and the final
    // bubble dropped.
    const serverMessages = [
      textMessage("server-user-1", "user", "Question"),
      textMessage("server-assistant-1a", "assistant", "Same "),
      textMessage("server-assistant-1b", "assistant", "answer"),
      textMessage("server-user-2", "user", "Question again"),
    ]

    expect(
      decideServerTranscriptAdoption({ serverMessages, liveMessages })
    ).toBe("reject-content")
  })

  it("rejects a stale snapshot that omits the whole final turn including its prompt", () => {
    const liveMessages = [
      textMessage("live-user-1", "user", "Question"),
      textMessage("live-assistant-1", "assistant", "Same answer"),
      textMessage("live-user-2", "user", "Question again"),
      textMessage("live-assistant-2", "assistant", "Same answer"),
    ]
    // The mid-turn DB filter hides every row of the active run, so the stale
    // snapshot lacks even the final turn's user prompt. Old-turn segmentation
    // satisfies the count guard and the previous answer repeats the final
    // text, so only the prompt anchor can prove the turn is missing.
    const serverMessages = [
      textMessage("server-user-1", "user", "Question"),
      textMessage("server-assistant-1a", "assistant", "Same "),
      textMessage("server-assistant-1b", "assistant", "ans"),
      textMessage("server-assistant-1c", "assistant", "wer"),
    ]

    expect(
      decideServerTranscriptAdoption({ serverMessages, liveMessages })
    ).toBe("reject-content")
  })

  it("uses only the count guard when the live transcript ends with a user message", () => {
    // The finalize race protects the final streamed assistant turn; when the
    // last live message is a user prompt there is no such turn, so a previous
    // turn's assistant text must not be used as the coverage probe.
    const liveMessages = [
      textMessage("live-user-1", "user", "Question"),
      textMessage("live-assistant-1", "assistant", "Answer"),
      textMessage("live-user-2", "user", "New question"),
    ]
    const serverMessages = [
      textMessage("server-user-1", "user", "Question"),
      textMessage("server-assistant-1", "assistant", "Different text"),
      textMessage("server-user-2", "user", "New question"),
    ]

    expect(
      decideServerTranscriptAdoption({ serverMessages, liveMessages })
    ).toBe("adopt")
  })

  it("adopts cancelled-turn marker rows that retain the streamed partial text", () => {
    const queryClient = new QueryClient()
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Long request"),
      textMessage("live-assistant", "assistant", "Partial response"),
    ]
    const serverMessages = [
      textMessage("server-user", "user", "Long request"),
      textMessage("server-assistant", "assistant", "Partial response"),
      textMessage("server-cancelled", "assistant", "[Turn cancelled]"),
    ]

    const { unmount } = renderHook(
      () =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status: "ready",
          serverMessages,
          liveMessages,
          setMessages,
        }),
      { wrapper: createWrapper(queryClient) }
    )

    expect(setMessages).toHaveBeenCalledWith(serverMessages)
    expect(jest.getTimerCount()).toBe(0)
    unmount()
  })

  it("cancels retries when a new turn starts streaming", async () => {
    const queryClient = new QueryClient()
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Question"),
      textMessage("live-assistant", "assistant", "Final streamed answer"),
    ]
    const serverMessages = [
      textMessage("server-user", "user", "Question"),
      textMessage("server-assistant", "assistant", "Previous answer"),
    ]

    const { rerender, unmount } = renderHook(
      ({ status }: { status: ChatStatus }) =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status,
          serverMessages,
          liveMessages,
          setMessages,
        }),
      {
        initialProps: { status: "ready" as ChatStatus },
        wrapper: createWrapper(queryClient),
      }
    )

    expect(jest.getTimerCount()).toBe(3)
    rerender({ status: "streaming" })
    expect(jest.getTimerCount()).toBe(0)

    await advanceTimersBy(8_000)
    expect(invalidateQueries).not.toHaveBeenCalled()
    expect(setMessages).not.toHaveBeenCalled()
    unmount()
  })

  it("cancels retries when the hook unmounts", () => {
    const queryClient = new QueryClient()
    const setMessages = jest.fn()
    const liveMessages = [
      textMessage("live-user", "user", "Question"),
      textMessage("live-assistant", "assistant", "Final streamed answer"),
    ]
    const serverMessages = [
      textMessage("server-user", "user", "Question"),
      textMessage("server-assistant", "assistant", "Previous answer"),
    ]

    const { unmount } = renderHook(
      () =>
        useAdoptServerTranscript({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          status: "ready",
          serverMessages,
          liveMessages,
          setMessages,
        }),
      { wrapper: createWrapper(queryClient) }
    )

    expect(jest.getTimerCount()).toBe(3)
    unmount()
    expect(jest.getTimerCount()).toBe(0)
  })
})
