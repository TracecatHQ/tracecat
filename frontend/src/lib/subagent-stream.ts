import { readUIMessageStream, type UIMessage, type UIMessageChunk } from "ai"

/** Tool name the agent runtime uses to run a configured child agent. */
export const SUBAGENT_TOOL_NAME = "subagent"

/** Transient data part carrying one child session's UI message chunk. */
export const AGENT_CHUNK_DATA_PART_TYPE = "data-agent-chunk"

/**
 * Payload of a `data-agent-chunk` part. `(event_id, index)` identifies the
 * chunk: stream reconnects replay the turn from the start and repeat keys.
 */
export type AgentChunkData = {
  session_id: string
  event_id: string
  index: number
  chunk: UIMessageChunk
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const EMBEDDED_SESSION_ID_PATTERN =
  /session_id["']?\s*[:=]\s*["']([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})["']/i
const MAX_SESSION_ID_SEARCH_DEPTH = 4

/** Validate the payload of a `data-agent-chunk` part. */
export function parseAgentChunkData(data: unknown): AgentChunkData | null {
  if (!data || typeof data !== "object") {
    return null
  }
  const { session_id, event_id, index, chunk } = data as Record<string, unknown>
  if (
    typeof session_id !== "string" ||
    typeof event_id !== "string" ||
    typeof index !== "number" ||
    !chunk ||
    typeof chunk !== "object" ||
    typeof (chunk as { type?: unknown }).type !== "string"
  ) {
    return null
  }
  return { session_id, event_id, index, chunk: chunk as UIMessageChunk }
}

/**
 * Find the child session id in a `subagent` tool output.
 *
 * Live outputs carry it as `output.session_id` (preliminary and final). Error
 * outputs and persisted history can nest it inside an `errorText` object, MCP
 * text blocks, or a serialized string, so this searches a few levels deep.
 */
export function getSubagentSessionId(
  output: unknown,
  depth = 0
): string | null {
  if (output == null || depth > MAX_SESSION_ID_SEARCH_DEPTH) {
    return null
  }
  if (typeof output === "string") {
    const trimmed = output.trim()
    if (!trimmed) {
      return null
    }
    try {
      return getSubagentSessionId(JSON.parse(trimmed), depth + 1)
    } catch {
      return trimmed.match(EMBEDDED_SESSION_ID_PATTERN)?.[1] ?? null
    }
  }
  if (Array.isArray(output)) {
    for (const item of output) {
      const sessionId = getSubagentSessionId(item, depth + 1)
      if (sessionId) {
        return sessionId
      }
    }
    return null
  }
  if (typeof output !== "object") {
    return null
  }
  const record = output as Record<string, unknown>
  const direct = record.session_id
  if (typeof direct === "string" && UUID_PATTERN.test(direct)) {
    return direct
  }
  for (const value of Object.values(record)) {
    const sessionId = getSubagentSessionId(value, depth + 1)
    if (sessionId) {
      return sessionId
    }
  }
  return null
}

type SubagentSession = {
  seenKeys: Set<string>
  /** Null until the first chunk arrives, and again once the reader stops. */
  controller: ReadableStreamDefaultController<UIMessageChunk> | null
  started: boolean
  message: UIMessage | undefined
  listeners: Set<() => void>
}

function normalizeSessionId(sessionId: string): string {
  return sessionId.toLowerCase()
}

/**
 * Live child-session transcripts built from `data-agent-chunk` parts.
 *
 * Each child session gets its own UI message stream fed through
 * `readUIMessageStream`, so child chunks never touch the parent transcript.
 * Chunks already applied are dropped by `(event_id, index)`, which makes a
 * full replay after a reconnect a no-op.
 */
export class SubagentStreamStore {
  private readonly sessions = new Map<string, SubagentSession>()

  /** Route one child chunk into its session's stream, dropping replays. */
  ingest(data: AgentChunkData): void {
    const session = this.getOrCreateSession(data.session_id)
    const key = `${data.index}:${data.event_id}`
    if (session.seenKeys.has(key)) {
      return
    }
    session.seenKeys.add(key)
    if (!session.started) {
      this.startReader(session)
    }
    try {
      session.controller?.enqueue(data.chunk)
    } catch (error) {
      // The reader stopped after a processing error; keep the last message.
      session.controller = null
      console.error("Failed to apply subagent stream chunk", error)
    }
  }

  /** Latest live message for a child session, if any chunks have arrived. */
  getMessage(sessionId: string): UIMessage | undefined {
    return this.sessions.get(normalizeSessionId(sessionId))?.message
  }

  /** Subscribe to live message updates for a child session. */
  subscribe(sessionId: string, listener: () => void): () => void {
    const session = this.getOrCreateSession(sessionId)
    session.listeners.add(listener)
    return () => {
      session.listeners.delete(listener)
    }
  }

  private getOrCreateSession(sessionId: string): SubagentSession {
    const key = normalizeSessionId(sessionId)
    const existing = this.sessions.get(key)
    if (existing) {
      return existing
    }
    const session: SubagentSession = {
      seenKeys: new Set(),
      controller: null,
      started: false,
      message: undefined,
      listeners: new Set(),
    }
    this.sessions.set(key, session)
    return session
  }

  private startReader(session: SubagentSession): void {
    session.started = true
    const stream = new ReadableStream<UIMessageChunk>({
      start(controller) {
        session.controller = controller
      },
    })
    void readSession(session, stream)
  }
}

async function readSession(
  session: SubagentSession,
  stream: ReadableStream<UIMessageChunk>
): Promise<void> {
  try {
    for await (const message of readUIMessageStream({
      stream,
      onError: (error) =>
        console.error("Failed to process subagent stream", error),
    })) {
      session.message = message
      for (const listener of session.listeners) {
        listener()
      }
    }
  } catch (error) {
    console.error("Subagent stream ended unexpectedly", error)
  } finally {
    session.controller = null
  }
}
