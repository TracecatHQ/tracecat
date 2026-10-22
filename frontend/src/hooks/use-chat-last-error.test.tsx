import { useChat } from "@ai-sdk/react"
import { act, renderHook } from "@testing-library/react"
import type { UIMessage } from "ai"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

let mockMessages: UIMessage[] = []
let mockStatus = "ready"
const mockRegenerate = jest.fn(() => Promise.resolve())
const mockResumeStream = jest.fn(() => Promise.resolve())

jest.mock("@ai-sdk/react", () => ({
  useChat: jest.fn(() => ({
    status: mockStatus,
    messages: mockMessages,
    resumeStream: mockResumeStream,
    sendMessage: () => Promise.resolve(),
    regenerate: mockRegenerate,
  })),
}))

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

// jsdom has no fetch Response, which parseChatError checks against.
global.Response ??= class {} as unknown as typeof Response

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  )
}

function renderChat() {
  const hook = renderHook(
    () =>
      useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
      }),
    { wrapper }
  )
  const options = jest.mocked(useChat).mock.calls.at(-1)?.[0]
  if (!options || !("onError" in options) || !("onFinish" in options)) {
    throw new Error("useChat options not captured")
  }
  return { hook, onError: options.onError, onFinish: options.onFinish }
}

function finish(
  onFinish: ReturnType<typeof renderChat>["onFinish"],
  flags: { isError: boolean; isDisconnect?: boolean }
) {
  onFinish?.({
    message: { id: "reply", role: "assistant", parts: [] },
    messages: [],
    isAbort: false,
    isDisconnect: flags.isDisconnect ?? false,
    isError: flags.isError,
  })
}

describe("useVercelChat lastError", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.mocked(useChat).mockClear()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it("keeps a dropped stream's error after the SDK runs onFinish", () => {
    const { hook, onError, onFinish } = renderChat()

    act(() => {
      onError?.(new TypeError("Failed to fetch"))
      finish(onFinish, { isError: true, isDisconnect: true })
    })

    expect(hook.result.current.lastError).toBe("Failed to fetch")
    expect(hook.result.current.lastErrorDisconnected).toBe(true)
  })

  it("clears the error once a later turn finishes cleanly", () => {
    const { hook, onError, onFinish } = renderChat()

    act(() => {
      onError?.(new Error("Provider unavailable"))
      finish(onFinish, { isError: true })
    })
    act(() => finish(onFinish, { isError: false }))

    expect(hook.result.current.lastError).toBeNull()
  })
})

describe("useVercelChat dropped prompt across reloads", () => {
  const prior: UIMessage[] = [
    { id: "u1", role: "user", parts: [{ type: "text", text: "Hi" }] },
    { id: "a1", role: "assistant", parts: [{ type: "text", text: "Hello" }] },
  ]
  const unsent: UIMessage = {
    id: "u2",
    role: "user",
    parts: [{ type: "text", text: "Summarize it" }],
  }

  beforeEach(() => {
    jest.useFakeTimers()
    jest.mocked(useChat).mockClear()
    window.localStorage.clear()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  function reload(serverMessages: UIMessage[]) {
    jest.mocked(useChat).mockClear()
    const hook = renderHook(
      () =>
        useVercelChat({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          messages: serverMessages,
        }),
      { wrapper }
    )
    return { hook, seeded: jest.mocked(useChat).mock.calls.at(-1)?.[0] }
  }

  async function dropTurn(turnMessages: UIMessage[] = [...prior, unsent]) {
    const { hook, onError, onFinish } = renderChat()
    await act(() => hook.result.current.sendMessage({ text: "Summarize it" }))
    act(() => {
      onError?.(new TypeError("Failed to fetch"))
      onFinish?.({
        message: { id: "reply", role: "assistant", parts: [] },
        messages: turnMessages,
        isAbort: false,
        isDisconnect: true,
        isError: true,
      })
    })
    hook.unmount()
  }

  it("restores a dropped prompt the server never got, with its error", async () => {
    await dropTurn()

    const { hook, seeded } = reload(prior)

    expect(seeded && "messages" in seeded && seeded.messages).toEqual([
      ...prior,
      unsent,
    ])
    expect(hook.result.current.lastErrorDisconnected).toBe(true)
  })

  it("drops the saved prompt once the server transcript holds it", async () => {
    await dropTurn()

    const { hook, seeded } = reload([...prior, { ...unsent, id: "server-u2" }])

    expect(seeded && "messages" in seeded && seeded.messages).toHaveLength(3)
    expect(hook.result.current.lastError).toBeNull()
    expect(
      window.localStorage.getItem("tracecat:chat:dropped-prompt:chat-1")
    ).toBeNull()
  })

  it("does not keep a dropped approval continuation", async () => {
    await dropTurn([
      ...prior,
      unsent,
      {
        id: "continue-1",
        role: "user",
        parts: [{ type: "data-continue", data: { decisions: [] } }],
      },
    ])

    const { hook } = reload(prior)

    expect(hook.result.current.lastError).toBeNull()
  })

  it("keeps the saved prompt when a reconnect replay drops", async () => {
    await dropTurn()
    // A replay of the running turn shows only the previous turn's prompt.
    const { hook, onError, onFinish } = renderChat()
    act(() => {
      onError?.(new TypeError("Failed to fetch"))
      onFinish?.({
        message: { id: "reply", role: "assistant", parts: [] },
        messages: prior,
        isAbort: false,
        isDisconnect: true,
        isError: true,
      })
    })
    hook.unmount()

    const { seeded } = reload(prior)

    expect(seeded && "messages" in seeded && seeded.messages).toEqual([
      ...prior,
      unsent,
    ])
  })

  // The SDK delivers a left chat's abort to the current chat's callbacks.
  it("keeps the restored prompt through an aborted request", async () => {
    await dropTurn()
    const { hook, seeded } = reload(prior)
    const onFinish = seeded && "onFinish" in seeded ? seeded.onFinish : null
    act(() =>
      onFinish?.({
        message: { id: "reply", role: "assistant", parts: [] },
        messages: [],
        isAbort: true,
        isDisconnect: false,
        isError: false,
      })
    )

    expect(hook.result.current.lastErrorDisconnected).toBe(true)
    hook.unmount()
    const { seeded: again } = reload(prior)
    expect(again && "messages" in again && again.messages).toEqual([
      ...prior,
      unsent,
    ])
  })

  it("rechecks the server before resending when a reload's reconnect fails", async () => {
    await dropTurn()
    const { hook, seeded } = reload(prior)
    if (!seeded || !("onError" in seeded)) {
      throw new Error("useChat options not captured")
    }
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 204,
      body: null,
    }) as unknown as typeof fetch
    mockMessages = [...prior, unsent]
    mockRegenerate.mockClear()
    // The SDK runs only onError when the reconnect request itself fails;
    // Safari words that failure "Load failed".
    act(() => seeded.onError?.(new TypeError("Load failed")))

    expect(hook.result.current.lastErrorDisconnected).toBe(true)
    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(mockRegenerate).toHaveBeenCalledWith({ messageId: "u2" })
    expect(jest.mocked(global.fetch).mock.invocationCallOrder[0]).toBeLessThan(
      mockRegenerate.mock.invocationCallOrder[0]
    )
    mockMessages = []
  })

  it("never resends when the reconnect Retry started gets an HTTP error", async () => {
    await dropTurn()
    const { hook, seeded } = reload(prior)
    if (!seeded || !("onError" in seeded)) {
      throw new Error("useChat options not captured")
    }
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: null,
    }) as unknown as typeof fetch
    mockMessages = [...prior, unsent]
    mockRegenerate.mockClear()
    act(() => seeded.onError?.(new TypeError("Failed to fetch")))
    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })
    const fresh = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    if (!fresh || !("onError" in fresh)) {
      throw new Error("useChat options not captured")
    }

    act(() => fresh.onError?.(new Error("Bad gateway")))
    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(mockRegenerate).not.toHaveBeenCalled()
    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#2",
    })
    mockMessages = []
  })

  it("never resends when the reconnect Retry started fails", async () => {
    await dropTurn()
    const { hook, seeded } = reload(prior)
    if (!seeded || !("onError" in seeded)) {
      throw new Error("useChat options not captured")
    }
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: null,
    }) as unknown as typeof fetch
    mockMessages = [...prior, unsent]
    mockRegenerate.mockClear()
    act(() => seeded.onError?.(new TypeError("Failed to fetch")))
    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })
    const fresh = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    if (!fresh || !("onError" in fresh)) {
      throw new Error("useChat options not captured")
    }

    act(() => fresh.onError?.(new TypeError("Failed to fetch")))
    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(mockRegenerate).not.toHaveBeenCalled()
    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#2",
    })
    mockMessages = []
  })

  it("rechecks the server when a reload's reconnect gets an HTTP error", async () => {
    global.fetch = jest.fn() as unknown as typeof fetch
    mockRegenerate.mockClear()
    // The SDK keeps status untouched until GET /stream returns.
    const { hook, onError } = renderChat()
    act(() => onError?.(new Error("Bad gateway")))

    expect(hook.result.current.lastErrorDisconnected).toBe(true)
    await act(async () => {
      await hook.result.current.retryTurn("u1")
    })

    expect(mockRegenerate).not.toHaveBeenCalled()
    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#1",
    })
  })

  it("keeps a replayed run failure red", () => {
    const { hook, onError, onFinish } = renderChat()
    mockStatus = "streaming"
    hook.rerender()
    act(() => {
      onError?.(new Error("Provider unavailable"))
      finish(onFinish, { isError: true })
    })
    mockStatus = "ready"

    expect(hook.result.current.lastError).toBe("Provider unavailable")
    expect(hook.result.current.lastErrorDisconnected).toBe(false)
  })

  it("never resends when a reconnect without a restored prompt fails", async () => {
    global.fetch = jest.fn() as unknown as typeof fetch
    mockRegenerate.mockClear()
    const { hook, onError } = renderChat()
    act(() => onError?.(new TypeError("Failed to fetch")))

    await act(async () => {
      await hook.result.current.retryTurn("u1")
    })

    expect(mockRegenerate).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#1",
    })
  })

  it("keeps the restored prompt when a reload's reconnect finishes cleanly", async () => {
    await dropTurn()
    // A run that failed before saving the prompt replays as a clean finish.
    const { onFinish } = renderChat()
    act(() => finish(onFinish, { isError: false }))

    const { seeded } = reload(prior)

    expect(seeded && "messages" in seeded && seeded.messages).toEqual([
      ...prior,
      unsent,
    ])
  })

  it("forgets the prompt after a later turn fails", async () => {
    await dropTurn()
    const { hook: later, onFinish } = renderChat()
    await act(() => later.result.current.sendMessage({ text: "Next" }))
    act(() => finish(onFinish, { isError: true }))

    const { hook } = reload(prior)

    expect(hook.result.current.lastError).toBeNull()
  })

  it("forgets the prompt after a later turn finishes cleanly", async () => {
    await dropTurn()
    const { hook: later, onFinish } = renderChat()
    await act(() => later.result.current.sendMessage({ text: "Next" }))
    act(() => finish(onFinish, { isError: false }))

    const { hook } = reload(prior)

    expect(hook.result.current.lastError).toBeNull()
  })
})

describe("useVercelChat retryTurn", () => {
  const prompt: UIMessage = {
    id: "u2",
    role: "user",
    parts: [{ type: "text", text: "Summarize it" }],
  }

  beforeEach(() => {
    jest.mocked(useChat).mockClear()
    mockRegenerate.mockClear()
    mockMessages = [prompt]
    window.localStorage.clear()
  })

  afterEach(() => {
    mockMessages = []
  })

  it("reconnects through a fresh chat when the server took the prompt", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: null,
    }) as unknown as typeof fetch
    const { hook, onError, onFinish } = renderChat()
    await act(() => hook.result.current.sendMessage({ text: "Summarize it" }))
    act(() => {
      onError?.(new TypeError("Failed to fetch"))
      finish(onFinish, { isError: true, isDisconnect: true })
    })

    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#1",
    })
    expect(mockRegenerate).not.toHaveBeenCalled()

    // The reconnect's own replay drops too: Retry reconnects again.
    const fresh = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    if (!fresh || !("onError" in fresh) || !("onFinish" in fresh)) {
      throw new Error("useChat options not captured")
    }
    act(() => {
      fresh.onError?.(new TypeError("Failed to fetch"))
      finish(fresh.onFinish, { isError: true, isDisconnect: true })
    })
    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#2",
    })
    expect(mockRegenerate).not.toHaveBeenCalled()
  })

  it("never resends after a reconnect replay drops", async () => {
    global.fetch = jest.fn() as unknown as typeof fetch
    // The mount reconnect replays a running turn without its prompt.
    const { hook, onError, onFinish } = renderChat()
    act(() => {
      onError?.(new TypeError("Failed to fetch"))
      finish(onFinish, { isError: true, isDisconnect: true })
    })

    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#1",
    })
    expect(mockRegenerate).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it("reconnects a chat opened with resume off", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: null,
    }) as unknown as typeof fetch
    const { result } = renderHook(
      () =>
        useVercelChat({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          messages: [],
          resume: false,
        }),
      { wrapper }
    )
    const options = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    if (!options || !("onError" in options) || !("onFinish" in options)) {
      throw new Error("useChat options not captured")
    }
    act(() => {
      options.onError?.(new TypeError("Failed to fetch"))
      finish(options.onFinish, { isError: true, isDisconnect: true })
    })
    mockResumeStream.mockClear()

    await act(async () => {
      await result.current.retryTurn("u2")
    })

    expect(mockResumeStream).toHaveBeenCalledTimes(1)
  })

  it("never resends a dropped approval continuation", async () => {
    global.fetch = jest.fn() as unknown as typeof fetch
    mockMessages = [
      {
        id: "continue-1",
        role: "user",
        parts: [{ type: "data-continue", data: { decisions: [] } }],
      },
    ]
    const { hook, onError, onFinish } = renderChat()
    await act(() => hook.result.current.sendMessage({ text: "" }))
    act(() => {
      onError?.(new TypeError("Failed to fetch"))
      finish(onFinish, { isError: true, isDisconnect: true })
    })

    await act(async () => {
      await hook.result.current.retryTurn("continue-1")
    })

    expect(mockRegenerate).not.toHaveBeenCalled()
    expect(global.fetch).not.toHaveBeenCalled()
    expect(jest.mocked(useChat).mock.calls.at(-1)?.[0]).toMatchObject({
      id: "chat-1#1",
    })
  })

  it("resends after a failure that kept the connection", async () => {
    const { hook, onError, onFinish } = renderChat()
    await act(() => hook.result.current.sendMessage({ text: "Summarize it" }))
    act(() => {
      onError?.(new Error("Provider unavailable"))
      finish(onFinish, { isError: true })
    })

    await act(async () => {
      await hook.result.current.retryTurn("u2")
    })

    expect(mockRegenerate).toHaveBeenCalledWith({ messageId: "u2" })
    expect(hook.result.current.lastError).toBeNull()
  })
})

describe("useVercelChat lastError across chats", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.mocked(useChat).mockClear()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it("does not resurface a chat's error after switching away and back", () => {
    const { result, rerender } = renderHook(
      ({ chatId }) =>
        useVercelChat({ chatId, workspaceId: "workspace-1", messages: [] }),
      { initialProps: { chatId: "chat-1" }, wrapper }
    )
    const options = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    const onError = options && "onError" in options ? options.onError : null
    act(() => onError?.(new TypeError("Failed to fetch")))
    expect(result.current.lastError).toBe("Failed to fetch")

    rerender({ chatId: "chat-2" })
    rerender({ chatId: "chat-1" })

    expect(result.current.lastError).toBeNull()
  })

  it("keeps a live drop's error when a refetch shows the prompt", () => {
    const prompt: UIMessage = {
      id: "u1",
      role: "user",
      parts: [{ type: "text", text: "Summarize it" }],
    }
    const { result, rerender } = renderHook(
      ({ messages }) =>
        useVercelChat({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          messages,
        }),
      { initialProps: { messages: [] as UIMessage[] }, wrapper }
    )
    const options = jest.mocked(useChat).mock.calls.at(-1)?.[0]
    if (!options || !("onError" in options) || !("onFinish" in options)) {
      throw new Error("useChat options not captured")
    }
    act(() => {
      options.onError?.(new TypeError("Failed to fetch"))
      options.onFinish?.({
        message: { id: "reply", role: "assistant", parts: [] },
        messages: [prompt],
        isAbort: false,
        isDisconnect: true,
        isError: true,
      })
    })

    rerender({ messages: [prompt] })

    expect(result.current.lastErrorDisconnected).toBe(true)
  })
})
