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
  | { kind: "attach"; streamId: string | null }
  | { kind: "refresh" }

/** What this tab last learned about the session's run. */
export type FeedState = {
  /** The segment this tab last attached to through the feed. */
  attachedStreamId: string | null
  /** The run state from the latest event, or null before the first snapshot. */
  known: {
    runId: string | null
    paused: boolean
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
 * to is not attached again; a reconnect snapshot refreshes only when the run
 * state changed or the feed moved on while this tab was away.
 */
export function feedActionFor(
  event: SessionFeedEvent,
  state: FeedState
): FeedAction | null {
  switch (event.type) {
    case "snapshot": {
      if (event.run_id && !event.paused) {
        const attach = attachUnlessHeld(event.stream_id, state)
        if (attach) return attach
      }
      const known = state.known
      const changed =
        known !== null &&
        (known.runId !== event.run_id ||
          known.paused !== event.paused ||
          known.feedId !== (event.feed_id ?? null))
      return changed ? { kind: "refresh" } : null
    }
    case "run.started":
    case "run.resumed":
      return attachUnlessHeld(event.stream_id, state)
    case "run.paused":
    case "run.finished":
      return { kind: "refresh" }
  }
}

function attachUnlessHeld(
  streamId: string | null,
  state: FeedState
): FeedAction | null {
  if (streamId !== null && streamId === state.attachedStreamId) return null
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
        feedId: event.feed_id ?? null,
      }
    case "run.started":
    case "run.resumed":
      return { runId: event.run_id, paused: false, feedId: entryId }
    case "run.paused":
      return { runId: event.run_id, paused: true, feedId: entryId }
    case "run.finished":
      return { runId: null, paused: false, feedId: entryId }
  }
}

/**
 * Follow a session's run state, so runs started elsewhere reach this tab.
 * While `busy`, the latest action waits and runs once the tab is idle. The
 * feed closes while the tab is hidden and reopens with a fresh snapshot.
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
  onAttach: () => void
  onRefresh: () => void
}) {
  const handlersRef = useRef({ onAttach, onRefresh })
  handlersRef.current = { onAttach, onRefresh }
  const busyRef = useRef(busy)
  busyRef.current = busy
  const stateRef = useRef<FeedState>({ attachedStreamId: null, known: null })
  const deferredRef = useRef<FeedAction | null>(null)

  function run(action: FeedAction) {
    if (busyRef.current) {
      deferredRef.current = action
      return
    }
    deferredRef.current = null
    if (action.kind === "attach") {
      stateRef.current.attachedStreamId = action.streamId
      handlersRef.current.onAttach()
    } else {
      handlersRef.current.onRefresh()
    }
  }
  const runRef = useRef(run)
  runRef.current = run

  useEffect(() => {
    if (!busy && deferredRef.current) runRef.current(deferredRef.current)
  }, [busy])

  useEffect(() => {
    stateRef.current = { attachedStreamId: null, known: null }
    deferredRef.current = null
    if (!chatId || typeof EventSource === "undefined") return
    const url = new URL(`/api/agent/sessions/${chatId}/feed`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)
    let source: EventSource | null = null

    function open() {
      source?.close()
      source = new EventSource(url.toString(), { withCredentials: true })
      source.onmessage = (message: MessageEvent<string>) => {
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
        source?.close()
        source = null
      } else if (!source || source.readyState === EventSource.CLOSED) {
        open()
      }
    }

    if (document.visibilityState !== "hidden") open()
    document.addEventListener("visibilitychange", onVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange)
      source?.close()
    }
  }, [chatId, workspaceId])
}
