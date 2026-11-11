import { useEffect, useRef } from "react"
import { z } from "zod"
import { getBaseUrl } from "@/lib/api"

const runEventSchema = z.object({
  type: z.enum(["run.started", "run.paused", "run.resumed", "run.finished"]),
  run_id: z.string().min(1),
  stream_id: z.string().nullable(),
})

const snapshotSchema = z.object({
  type: z.literal("snapshot"),
  run_id: z.string().nullable(),
  stream_id: z.string().nullable(),
  paused: z.boolean(),
  /** The newest feed entry id when this connection opened. */
  feed_id: z.string().nullable().optional(),
})

const feedEventSchema = z.discriminatedUnion("type", [
  runEventSchema,
  snapshotSchema,
])

/** Anything `GET /agent/sessions/{id}/feed` delivers. */
export type SessionFeedEvent = z.infer<typeof feedEventSchema>

/** What a feed event asks an idle tab to do. */
export type FeedAction =
  | { kind: "attach"; streamId: string }
  | { kind: "refresh" }

/** What this tab last learned about the session's run. */
export type FeedState = {
  /** The segment this tab last attached to and received output from. */
  attachedStreamId: string | null
  /** The segment this tab last tried to attach to; snapshots never retry it. */
  attemptedStreamId: string | null
  /** The run state from the latest event, or null before the first snapshot. */
  known: {
    runId: string | null
    paused: boolean
    streamId: string | null
    /** The feed entry id last seen; a newer one means events were missed. */
    feedId: string | null
  } | null
}

/** Parse one feed message, or null when it is not a known event. */
export function parseSessionFeedEvent(data: string): SessionFeedEvent | null {
  try {
    const parsed = feedEventSchema.safeParse(JSON.parse(data))
    return parsed.success ? parsed.data : null
  } catch {
    return null
  }
}

/**
 * Decide what a feed event asks of the tab. A segment the tab already attached
 * to is not attached again. The first snapshot refreshes; a reconnect snapshot
 * refreshes only when the run state changed or the feed moved on while this
 * tab was away.
 */
export function feedActionFor(
  event: SessionFeedEvent,
  state: FeedState
): FeedAction | null {
  switch (event.type) {
    case "snapshot": {
      // A row whose run ended without releasing it still names the run, so a
      // snapshot tries a segment once; only a run event retries it.
      if (
        event.run_id &&
        !event.paused &&
        event.stream_id &&
        event.stream_id !== state.attemptedStreamId
      ) {
        const attach = attachUnlessHeld(event.stream_id, state)
        if (attach) return attach
      }
      const known = state.known
      // The transcript loaded before the feed's cursor; anything in between is
      // not replayed, so the first snapshot always refreshes.
      if (known === null) return { kind: "refresh" }
      const changed =
        known.runId !== event.run_id ||
        known.paused !== event.paused ||
        known.streamId !== event.stream_id ||
        known.feedId !== (event.feed_id ?? null)
      return changed ? { kind: "refresh" } : null
    }
    case "run.started":
    case "run.resumed":
      // Without its own segment, a run shares the session buffer, which can
      // still hold the previous run; show the transcript instead.
      if (event.stream_id === null) return { kind: "refresh" }
      return attachUnlessHeld(event.stream_id, state)
    case "run.paused":
    case "run.finished":
      return { kind: "refresh" }
  }
}

function attachUnlessHeld(
  streamId: string,
  state: FeedState
): FeedAction | null {
  if (streamId === state.attachedStreamId) return null
  return { kind: "attach", streamId }
}

/** The run state after `event`; `entryId` is the SSE id a run event carried. */
export function knownStateAfter(
  event: SessionFeedEvent,
  entryId: string | null
): FeedState["known"] {
  switch (event.type) {
    case "snapshot":
      return {
        runId: event.run_id,
        paused: event.paused,
        streamId: event.stream_id,
        feedId: event.feed_id ?? null,
      }
    case "run.started":
    case "run.resumed":
      return {
        runId: event.run_id,
        paused: false,
        streamId: event.stream_id,
        feedId: entryId,
      }
    case "run.paused":
      return {
        runId: event.run_id,
        paused: true,
        streamId: event.stream_id,
        feedId: entryId,
      }
    case "run.finished":
      return { runId: null, paused: false, streamId: null, feedId: entryId }
  }
}

// EventSource gives up on a non-200 (a 503 during a Redis blip); reopen a few
// times with backoff. Showing the tab again always reopens.
const FEED_REOPEN_BASE_MS = 1_000
const FEED_REOPEN_MAX_MS = 30_000
const FEED_MAX_REOPENS = 6

/**
 * Follow a session's run state, so runs started elsewhere reach this tab.
 * While `busy`, the latest action waits and runs once the tab is idle. A
 * segment counts as attached only once `onAttach` settles it as held, so an
 * attach that found nothing to stream is retried by later events. The
 * feed closes while the tab is hidden and reopens with a fresh snapshot; a
 * failed connection reopens a bounded number of times.
 */
export function useSessionFeed({
  chatId,
  workspaceId,
  busy,
  onAttach,
  onRefresh,
}: {
  chatId?: string
  workspaceId: string
  /** A request or reconnect is in flight. */
  busy: boolean
  /**
   * Attach to the live segment, then call `settle` with whether the tab now
   * holds it. Call it before `busy` clears, or not at all if nothing started.
   */
  onAttach: (settle: (held: boolean) => void) => void
  onRefresh: () => void
}) {
  const handlersRef = useRef({ onAttach, onRefresh })
  handlersRef.current = { onAttach, onRefresh }
  const busyRef = useRef(busy)
  busyRef.current = busy
  const stateRef = useRef<FeedState>({
    attachedStreamId: null,
    attemptedStreamId: null,
    known: null,
  })
  const deferredRef = useRef<FeedAction | null>(null)

  function run(action: FeedAction) {
    if (busyRef.current) {
      deferredRef.current = action
      return
    }
    deferredRef.current = null
    if (action.kind === "refresh") {
      handlersRef.current.onRefresh()
      return
    }
    const state = stateRef.current
    // The attach that kept the tab busy may have settled this segment already.
    if (action.streamId === state.attachedStreamId) return
    state.attemptedStreamId = action.streamId
    handlersRef.current.onAttach((held) => {
      if (held) state.attachedStreamId = action.streamId
    })
  }
  const runRef = useRef(run)
  runRef.current = run

  useEffect(() => {
    if (!busy && deferredRef.current) runRef.current(deferredRef.current)
  }, [busy])

  useEffect(() => {
    stateRef.current = {
      attachedStreamId: null,
      attemptedStreamId: null,
      known: null,
    }
    deferredRef.current = null
    if (!chatId || typeof EventSource === "undefined") return
    const url = new URL(`/api/agent/sessions/${chatId}/feed`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)
    let source: EventSource | null = null
    let reopenTimer: ReturnType<typeof setTimeout> | null = null
    let failures = 0

    function cancelReopen() {
      if (reopenTimer !== null) clearTimeout(reopenTimer)
      reopenTimer = null
    }

    function scheduleReopen() {
      if (reopenTimer !== null || failures >= FEED_MAX_REOPENS) return
      const delay = Math.min(
        FEED_REOPEN_BASE_MS * 2 ** failures,
        FEED_REOPEN_MAX_MS
      )
      failures += 1
      reopenTimer = setTimeout(() => {
        reopenTimer = null
        open()
      }, delay)
    }

    function open() {
      cancelReopen()
      source?.close()
      const current = new EventSource(url.toString(), { withCredentials: true })
      source = current
      current.onopen = () => {
        failures = 0
      }
      // CONNECTING means the browser is retrying itself; CLOSED means it gave up.
      current.onerror = () => {
        if (source === current && current.readyState === EventSource.CLOSED) {
          scheduleReopen()
        }
      }
      current.onmessage = (message: MessageEvent<string>) => {
        const event = parseSessionFeedEvent(message.data)
        if (!event) return
        const action = feedActionFor(event, stateRef.current)
        stateRef.current.known = knownStateAfter(
          event,
          message.lastEventId || null
        )
        if (action) runRef.current(action)
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState === "hidden") {
        cancelReopen()
        source?.close()
        source = null
      } else if (!source || source.readyState === EventSource.CLOSED) {
        failures = 0
        open()
      }
    }

    if (document.visibilityState !== "hidden") open()
    document.addEventListener("visibilitychange", onVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange)
      cancelReopen()
      source?.close()
    }
  }, [chatId, workspaceId])
}
