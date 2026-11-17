import { TextDecoderStream } from "node:stream/web"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import {
  type FeedState,
  feedActionFor,
  knownStateAfter,
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
  onerror: (() => void) | null = null
  onmessage: ((message: MessageEvent<string>) => void) | null = null

  constructor(
    readonly url: string,
    readonly init?: EventSourceInit
  ) {
    FakeEventSource.instances.push(this)
  }

  /** Open the connection and deliver one feed message. */
  deliver(payload: object, lastEventId = "") {
    if (this.readyState !== FakeEventSource.OPEN) {
      this.readyState = FakeEventSource.OPEN
      this.onopen?.()
    }
    this.onmessage?.({
      data: JSON.stringify(payload),
      lastEventId,
    } as MessageEvent<string>)
  }

  close() {
    this.readyState = FakeEventSource.CLOSED
  }

  /** Fail the connection the way a non-200 response does. */
  fail() {
    this.readyState = FakeEventSource.CLOSED
    this.onerror?.()
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
  const idle: FeedState = {
    attachedStreamId: null,
    attemptedStreamId: null,
    known: null,
  }

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
      feedActionFor(started, {
        attachedStreamId: "s0",
        attemptedStreamId: null,
        known: null,
      })
    ).toBeNull()
    expect(
      feedActionFor(
        { type: "run.resumed", run_id: "r1", stream_id: "s1" },
        { attachedStreamId: "s0", attemptedStreamId: null, known: null }
      )
    ).toEqual({ kind: "attach", streamId: "s1" })
  })

  it("refreshes on a held live snapshot when the feed moved on", () => {
    const liveAt = (feedId: string) =>
      ({
        type: "snapshot",
        run_id: "r1",
        stream_id: "s0",
        paused: false,
        feed_id: feedId,
      }) as const
    const held: FeedState = {
      attachedStreamId: "s0",
      attemptedStreamId: null,
      known: { runId: "r1", paused: false, streamId: "s0", feedId: "3-0" },
    }
    expect(feedActionFor(liveAt("3-0"), held)).toBeNull()
    expect(feedActionFor(liveAt("5-0"), held)).toEqual({ kind: "refresh" })
  })

  it("refreshes on a snapshot only when the run state changed", () => {
    const idleSnapshot = {
      type: "snapshot",
      run_id: null,
      stream_id: null,
      paused: false,
    } as const
    expect(
      feedActionFor(idleSnapshot, {
        attachedStreamId: null,
        attemptedStreamId: null,
        known: { runId: null, paused: false, streamId: null, feedId: null },
      })
    ).toBeNull()
    expect(
      feedActionFor(idleSnapshot, {
        attachedStreamId: "s0",
        attemptedStreamId: null,
        known: { runId: "r1", paused: true, streamId: "s0", feedId: null },
      })
    ).toEqual({ kind: "refresh" })
  })

  it("refreshes when a run started and finished while the feed was down", () => {
    const idleAt = (feedId: string) =>
      ({
        type: "snapshot",
        run_id: null,
        stream_id: null,
        paused: false,
        feed_id: feedId,
      }) as const
    const seen: FeedState = {
      attachedStreamId: null,
      attemptedStreamId: null,
      known: knownStateAfter(
        { type: "run.finished", run_id: "r1", stream_id: null },
        "7-0"
      ),
    }
    expect(feedActionFor(idleAt("7-0"), seen)).toBeNull()
    expect(feedActionFor(idleAt("9-0"), seen)).toEqual({ kind: "refresh" })
  })

  it("refreshes on a paused snapshot whose segment moved", () => {
    const pausedOn = (streamId: string) =>
      ({
        type: "snapshot",
        run_id: "r1",
        stream_id: streamId,
        paused: true,
        feed_id: "4-0",
      }) as const
    const seen: FeedState = {
      attachedStreamId: "s0",
      attemptedStreamId: null,
      known: knownStateAfter(
        { type: "run.paused", run_id: "r1", stream_id: "s0" },
        "4-0"
      ),
    }
    expect(feedActionFor(pausedOn("s0"), seen)).toBeNull()
    expect(feedActionFor(pausedOn("s1"), seen)).toEqual({ kind: "refresh" })
  })

  it("retries a tried segment on run events but not snapshots", () => {
    const tried: FeedState = {
      attachedStreamId: null,
      attemptedStreamId: "s0",
      known: { runId: "r1", paused: false, streamId: "s0", feedId: null },
    }
    expect(
      feedActionFor(
        { type: "snapshot", run_id: "r1", stream_id: "s0", paused: false },
        tried
      )
    ).toBeNull()
    expect(
      feedActionFor(
        { type: "run.started", run_id: "r1", stream_id: "s0" },
        tried
      )
    ).toEqual({ kind: "attach", streamId: "s0" })
  })

  it("never attaches to a run without its own segment", () => {
    expect(
      feedActionFor(
        { type: "run.started", run_id: "r1", stream_id: null },
        idle
      )
    ).toEqual({ kind: "refresh" })
    expect(
      feedActionFor(
        { type: "snapshot", run_id: "r1", stream_id: null, paused: false },
        idle
      )
    ).toEqual({ kind: "refresh" })
  })

  it("refreshes on the first snapshot that does not attach", () => {
    // A run can finish or pause between loading the transcript and opening
    // the feed; only the first snapshot shows it.
    for (const snapshot of [
      { type: "snapshot", run_id: null, stream_id: null, paused: false },
      { type: "snapshot", run_id: "r1", stream_id: "s0", paused: true },
    ] as const) {
      expect(feedActionFor(snapshot, idle)).toEqual({ kind: "refresh" })
    }
    expect(
      feedActionFor(
        { type: "snapshot", run_id: "r1", stream_id: "s0", paused: false },
        idle
      )
    ).toEqual({ kind: "attach", streamId: "s0" })
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

  it("reopens a failed connection with bounded backoff", () => {
    jest.useFakeTimers()
    try {
      const { unmount } = renderHook(() =>
        useSessionFeed({
          chatId: "chat-1",
          workspaceId: "workspace-1",
          busy: false,
          onAttach: jest.fn(),
          onRefresh: jest.fn(),
        })
      )
      FakeEventSource.latest().fail()
      jest.advanceTimersByTime(999)
      expect(FakeEventSource.instances).toHaveLength(1)
      jest.advanceTimersByTime(1)
      expect(FakeEventSource.instances).toHaveLength(2)

      // A successful open resets the backoff.
      FakeEventSource.latest().deliver({
        type: "snapshot",
        run_id: null,
        stream_id: null,
        paused: false,
      })
      FakeEventSource.latest().fail()
      jest.advanceTimersByTime(1_000)
      expect(FakeEventSource.instances).toHaveLength(3)

      // Without an open, it stops after six attempts (one is already spent).
      for (let attempt = 0; attempt < 10; attempt++) {
        FakeEventSource.latest().fail()
        jest.advanceTimersByTime(30_000)
      }
      expect(FakeEventSource.instances).toHaveLength(8)

      unmount()
      jest.advanceTimersByTime(60_000)
      expect(FakeEventSource.instances).toHaveLength(8)
    } finally {
      jest.useRealTimers()
    }
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

/** A `/stream` response carrying these UI message chunks. */
function sseResponse(chunks: object[]): FakeResponse {
  const frames = [...chunks.map((c) => JSON.stringify(c)), "[DONE]"]
    .map((data) => `data: ${data}\n\n`)
    .join("")
  return {
    ok: true,
    status: 200,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames))
        controller.close()
      },
    }),
    text: async () => frames,
  }
}

/** What `/stream` sends for a turn Temporal cannot see yet or that ended. */
const FINISHED_ONLY = [{ type: "start", messageId: "m1" }, { type: "finish" }]

describe("useVercelChat with the session feed", () => {
  const fetchMock = jest.fn(
    (_url: string, _init: { method: string }): Promise<FakeResponse> =>
      Promise.resolve(NO_CONTENT)
  )

  beforeEach(() => {
    // onFinish schedules delayed title refreshes; keep them out of later tests.
    jest.useFakeTimers()
    fetchMock.mockClear()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
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

  it("still attaches after a send that failed without dropping", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        body: null,
        text: () => Promise.resolve("boom"),
      })
    )
    const { result } = renderChat(new QueryClient())

    await act(async () => {
      await result.current.sendMessage({ text: "hi" })
    })
    await waitFor(() => expect(result.current.status).toBe("error"))
    expect(result.current.lastErrorDisconnected).toBe(false)
    act(() => {
      FakeEventSource.latest().deliver({
        type: "run.started",
        run_id: "r1",
        stream_id: "s0",
      })
    })

    await waitFor(() => expect(streamRequests()).toHaveLength(1))
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

  it("re-attaches a segment whose reconnect found nothing to stream", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(sseResponse(FINISHED_ONLY))
    )
    const { result } = renderChat(new QueryClient())
    const started = { type: "run.started", run_id: "r1", stream_id: "s0" }

    // A snapshot between the ownership commit and the workflow start.
    act(() => {
      FakeEventSource.latest().deliver({
        ...started,
        type: "snapshot",
        paused: false,
      })
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
    await waitFor(() => expect(result.current.status).toBe("ready"))

    act(() => {
      FakeEventSource.latest().deliver(started)
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(2))
  })

  it("tries a segment from snapshots only once", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(sseResponse(FINISHED_ONLY))
    )
    const { result } = renderChat(new QueryClient())
    // A run that ended without releasing the session keeps naming its segment.
    const stuck = {
      type: "snapshot",
      run_id: "r1",
      stream_id: "s0",
      paused: false,
    }

    act(() => {
      FakeEventSource.latest().deliver(stuck)
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
    await waitFor(() => expect(result.current.status).toBe("ready"))

    for (let i = 0; i < 3; i++) {
      act(() => {
        FakeEventSource.latest().deliver(stuck)
      })
    }
    await act(async () => {})
    expect(streamRequests()).toHaveLength(1)
  })

  it("does not re-attach a segment that streamed", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        sseResponse([
          { type: "start", messageId: "m1" },
          { type: "text-start", id: "t1" },
          { type: "text-delta", id: "t1", delta: "hi" },
          { type: "text-end", id: "t1" },
          { type: "finish" },
        ])
      )
    )
    const { result } = renderChat(new QueryClient())
    const started = { type: "run.started", run_id: "r1", stream_id: "s0" }

    act(() => {
      FakeEventSource.latest().deliver(started)
    })
    await waitFor(() => expect(streamRequests()).toHaveLength(1))
    await waitFor(() => expect(result.current.status).toBe("ready"))

    act(() => {
      FakeEventSource.latest().deliver(started)
    })
    expect(streamRequests()).toHaveLength(1)
  })
})
