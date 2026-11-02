import { TextDecoderStream } from "node:stream/web"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

// jsdom lacks these; the AI SDK uses them to snapshot and parse the stream.
if (typeof globalThis.structuredClone !== "function") {
  globalThis.structuredClone = ((value: unknown) =>
    value === undefined
      ? undefined
      : JSON.parse(JSON.stringify(value))) as typeof structuredClone
}
if (typeof globalThis.Response !== "function") {
  globalThis.Response = class {} as unknown as typeof Response
}
if (typeof globalThis.TextDecoderStream !== "function") {
  globalThis.TextDecoderStream =
    TextDecoderStream as unknown as typeof globalThis.TextDecoderStream
}

type FakeResponse = {
  ok: boolean
  status: number
  body: ReadableStream<Uint8Array> | null
  text: () => Promise<string>
}

/** An SSE response body whose UI message chunks the test writes. */
function openEventStream() {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController
    },
  })
  return {
    response: { ok: true, status: 200, body, text: async () => "" },
    write(chunk: object) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`))
    },
    close() {
      controller.enqueue(encoder.encode("data: [DONE]\n\n"))
      controller.close()
    },
  }
}

describe("mount reconnect and an immediate send", () => {
  let resolveReconnect: (response: FakeResponse) => void
  const reply = openEventStream()
  const fetchMock = jest.fn((_url: string, init: { method: string }) =>
    init.method === "GET"
      ? new Promise<FakeResponse>((resolve) => {
          resolveReconnect = resolve
        })
      : Promise.resolve(reply.response)
  )

  beforeEach(() => {
    jest.useFakeTimers()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it("sends a first message after the reconnect settles and finishes cleanly", async () => {
    // A new chat mounts with its first message pending: the mount reconnect
    // and the send must not run as overlapping SDK requests.
    const consoleError = jest.spyOn(console, "error").mockImplementation()
    const queryClient = new QueryClient()
    const { result } = renderHook(
      () =>
        useVercelChat({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          messages: [],
          modelInfo: { name: "model", provider: "provider" },
        }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      }
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    let sent!: Promise<void>
    act(() => {
      sent = result.current.sendMessage({ text: "Hello" })
    })
    // No live turn: the reconnect finds nothing to attach to.
    await act(async () => {
      resolveReconnect({
        ok: true,
        status: 204,
        body: null,
        text: async () => "",
      })
    })
    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        expect.stringContaining("/api/agent/sessions/chat-1/messages"),
        expect.objectContaining({ method: "POST" })
      )
    )
    // Counts only the reply's turn: the reconnect finished on its own.
    const complete = jest.spyOn(result.current.subagentStore, "complete")
    reply.write({ type: "start", messageId: "reply-1" })
    reply.write({ type: "text-start", id: "text-1" })
    reply.write({ type: "text-delta", id: "text-1", delta: "Hi there" })
    reply.write({ type: "text-end", id: "text-1" })
    reply.write({ type: "finish" })
    // The reply is still open, so its turn has not finished.
    await waitFor(() =>
      expect(result.current.messages.at(-1)?.parts).toContainEqual(
        expect.objectContaining({ text: "Hi there" })
      )
    )
    expect(complete).not.toHaveBeenCalled()

    await act(async () => {
      reply.close()
      await sent
    })
    await waitFor(() => expect(result.current.status).toBe("ready"))
    expect(complete).toHaveBeenCalledTimes(1)
    expect(consoleError).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })
})
