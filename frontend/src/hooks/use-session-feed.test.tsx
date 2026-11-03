import { TextDecoderStream } from "node:stream/web"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import {
  type FeedState,
  feedActionFor,
  parseSessionFeedEvent,
  useSessionFeed,
} from "@/hooks/use-session-feed"
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

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  readyState = FakeEventSource.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((message: MessageEvent<string>) => void) | null = null

  constructor(
    readonly url: string,
    readonly init?: EventSourceInit
  ) {
    FakeEventSource.instances.push(this)
  }

  /** Open the connection and deliver one feed message. */
  deliver(payload: object) {
    if (this.readyState !== FakeEventSource.OPEN) {
      this.readyState = FakeEventSource.OPEN
      this.onopen?.()
    }
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)
  }

  close() {
    this.readyState = FakeEventSource.CLOSED
  }

  static latest(): FakeEventSource {
    const latest = FakeEventSource.instances.at(-1)
    if (!latest) throw new Error("No feed connection")
    return latest
  }
}

beforeEach(() => {
  FakeEventSource.instances = []
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
})

afterEach(() => {
  delete (globalThis as { EventSource?: unknown }).EventSource
})

describe("parseSessionFeedEvent", () => {
  it("reads snapshots and run events", () => {
    const snapshot = {
      type: "snapshot",
      run_id: null,
      stream_id: null,
      paused: false,
    }
    expect(parseSessionFeedEvent(JSON.stringify(snapshot))).toEqual(snapshot)
    expect(
      parseSessionFeedEvent(
        JSON.stringify({ type: "run.resumed", run_id: "r1", stream_id: "s1" })
      )
    ).toEqual({ type: "run.resumed", run_id: "r1", stream_id: "s1" })
  })

  it("drops unknown or malformed messages", () => {
    for (const data of [
      "not json",
      "null",
      JSON.stringify({ type: "run.exploded", run_id: "r1", stream_id: null }),
      JSON.stringify({ type: "run.started", run_id: null, stream_id: null }),
      JSON.stringify({ type: "snapshot", run_id: null, stream_id: null }),
    ]) {
      expect(parseSessionFeedEvent(data)).toBeNull()
    }
  })
})

describe("feedActionFor", () => {
  const idle: FeedState = { attachedStreamId: null, known: null }

  it("attaches to a live segment once", () => {
    const started = {
      type: "run.started",
      run_id: "r1",
      stream_id: "s0",
    } as const
    expect(feedActionFor(started, idle)).toEqual({
      kind: "attach",
      streamId: "s0",
    })
    expect(
      feedActionFor(started, { attachedStreamId: "s0", known: null })
    ).toBeNull()
    expect(
      feedActionFor(
        { type: "run.resumed", run_id: "r1", stream_id: "s1" },
        { attachedStreamId: "s0", known: null }
      )
    ).toEqual({ kind: "attach", streamId: "s1" })
  })

  it("refreshes on a snapshot only when the run state changed", () => {
    const idleSnapshot = {
      type: "snapshot",
      run_id: null,
      stream_id: null,
      paused: false,
    } as const
    // The first snapshot after opening matches the transcript already shown.
    expect(feedActionFor(idleSnapshot, idle)).toBeNull()
    expect(
      feedActionFor(idleSnapshot, {
        attachedStreamId: null,
        known: { runId: null, paused: false },
      })
    ).toBeNull()
    expect(
      feedActionFor(idleSnapshot, {
        attachedStreamId: "s0",
        known: { runId: "r1", paused: true },
      })
    ).toEqual({ kind: "refresh" })
  })
})

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  })
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
  })
}

describe("useSessionFeed", () => {
  afterEach(() => setVisibility("visible"))

  it("opens one credentialed connection and closes it while hidden", () => {
    const { unmount } = renderHook(() =>
      useSessionFeed({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        busy: false,
        onAttach: jest.fn(),
        onRefresh: jest.fn(),
      })
    )
    const first = FakeEventSource.latest()
    expect(first.url).toBe(
      "https://example.invalid/api/agent/sessions/chat-1/feed?workspace_id=workspace-1"
    )
    expect(first.init).toEqual({ withCredentials: true })

    setVisibility("visible")
    expect(FakeEventSource.instances).toHaveLength(1)
    setVisibility("hidden")
    expect(first.readyState).toBe(FakeEventSource.CLOSED)
    setVisibility("visible")
    expect(FakeEventSource.instances).toHaveLength(2)

    unmount()
    expect(FakeEventSource.latest().readyState).toBe(FakeEventSource.CLOSED)
  })

  it("holds the latest action while busy and runs it once idle", () => {
    const onAttach = jest.fn()
    const onRefresh = jest.fn()
    const { rerender } = renderHook(
      ({ busy }: { busy: boolean }) =>
        useSessionFeed({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          busy,
          onAttach,
          onRefresh,
        }),
      { initialProps: { busy: true } }
    )
    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.started",
        run_id: "r1",
        stream_id: "s0",
      })
      FakeEventSource.latest().deliver({
        type: "run.finished",
        run_id: "r1",
        stream_id: null,
      })
    })
    expect(onRefresh).not.toHaveBeenCalled()

    rerender({ busy: false })
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(onAttach).not.toHaveBeenCalled()
  })
})

type FakeResponse = {
  ok: boolean
  status: number
  body: ReadableStream<Uint8Array> | null
  text: () => Promise<string>
}

const NO_CONTENT: FakeResponse = {
  ok: true,
  status: 204,
  body: null,
  text: async () => "",
}

describe("useVercelChat with the session feed", () => {
  const fetchMock = jest.fn(
    (_url: string, _init: { method: string }): Promise<FakeResponse> =>
      Promise.resolve(NO_CONTENT)
  )

  beforeEach(() => {
    fetchMock.mockClear()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })

  function renderChat(queryClient: QueryClient) {
    return renderHook(
      () =>
        useVercelChat({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          messages: [],
          resume: false,
        }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      }
    )
  }

  function streamRequests(): string[] {
    return fetchMock.mock.calls
      .map(([url]) => url)
      .filter((url) => url.includes("/api/agent/sessions/chat-1/stream"))
  }

  it("attaches to runs that start or resume elsewhere", async () => {
    const { result } = renderChat(new QueryClient())
    expect(streamRequests()).toHaveLength(0)

    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.started",
        run_id: "r1",
        stream_id: "s0",
      })
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
    await waitFor(() => expect(result.current.status).toBe("ready"))

    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.resumed",
        run_id: "r1",
        stream_id: "s1",
      })
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(2))
  })

  it("attaches on a snapshot of a live run but not a paused one", async () => {
    renderChat(new QueryClient())
    act(() => {
      FakeEventSource.latest().deliver({
        type: "snapshot",
        run_id: "r1",
        stream_id: "s0",
        paused: true,
      })
    })
    expect(streamRequests()).toHaveLength(0)

    act(() => {
      FakeEventSource.latest().deliver({
        type: "snapshot",
        run_id: "r1",
        stream_id: "s0",
        paused: false,
      })
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
  })

  it("refreshes the transcript when a run pauses or finishes elsewhere", () => {
    const queryClient = new QueryClient()
    const invalidate = jest.spyOn(queryClient, "invalidateQueries")
    renderChat(queryClient)

    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.finished",
        run_id: "r1",
        stream_id: null,
      })
    })

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["chat", "chat-1", "workspace-1", "vercel"],
    })
    expect(streamRequests()).toHaveLength(0)
  })

  /** Hold the first stream request open until the returned release is called. */
  function holdFirstStreamRequest(): () => void {
    let release = () => {}
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<FakeResponse>((resolve) => {
          release = () => resolve(NO_CONTENT)
        })
    )
    return () => release()
  }

  it("follows a segment that moves while the tab is still attaching", async () => {
    const release = holdFirstStreamRequest()
    const { result } = renderChat(new QueryClient())

    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.started",
        run_id: "r1",
        stream_id: "s0",
      })
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.resumed",
        run_id: "r1",
        stream_id: "s1",
      })
    })
    expect(streamRequests()).toHaveLength(1)

    await act(async () => release())
    await waitFor(() => expect(streamRequests()).toHaveLength(2))
    await waitFor(() => expect(result.current.status).toBe("ready"))
  })

  it("never replays into a chat whose stream dropped", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.reject(new TypeError("network error"))
    )
    const queryClient = new QueryClient()
    const invalidate = jest.spyOn(queryClient, "invalidateQueries")
    const { result } = renderChat(queryClient)

    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.started",
        run_id: "r1",
        stream_id: "s0",
      })
    })
    await waitFor(() => expect(result.current.status).toBe("error"))
    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.resumed",
        run_id: "r1",
        stream_id: "s1",
      })
    })

    expect(streamRequests()).toHaveLength(1)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["chat", "chat-1", "workspace-1", "vercel"],
    })
  })

  it("acts on only the latest event held while attaching", async () => {
    const release = holdFirstStreamRequest()
    const queryClient = new QueryClient()
    const invalidate = jest.spyOn(queryClient, "invalidateQueries")
    renderChat(queryClient)

    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.started",
        run_id: "r1",
        stream_id: "s0",
      })
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
    act(() => {
      for (const payload of [
        { type: "run.resumed", run_id: "r1", stream_id: "s1" },
        { type: "run.finished", run_id: "r1", stream_id: null },
      ]) {
        FakeEventSource.latest().deliver(payload)
      }
    })
    expect(invalidate).not.toHaveBeenCalled()

    await act(async () => release())
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["chat", "chat-1", "workspace-1", "vercel"],
      })
    )
    expect(streamRequests()).toHaveLength(1)
  })
})
