/**
 * @jest-environment node
 */
import type { UIMessage, UIMessageChunk } from "ai"
import {
  type AgentChunkData,
  getSubagentSessionId,
  parseAgentChunkData,
  SubagentStreamStore,
} from "@/lib/subagent-stream"

const CHILD_A = "0b5c6f7e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"
const CHILD_B = "5f0d3c2b-8a7e-4f61-b0c9-1e2d3c4b5a69"

function chunk(
  sessionId: string,
  eventId: string,
  index: number,
  value: UIMessageChunk
): AgentChunkData {
  return { session_id: sessionId, event_id: eventId, index, chunk: value }
}

function textChunks(sessionId: string, eventId: string, text: string) {
  return [
    chunk(sessionId, eventId, 0, { type: "text-start", id: `${eventId}:0` }),
    chunk(sessionId, eventId, 1, {
      type: "text-delta",
      id: `${eventId}:0`,
      delta: text,
    }),
  ]
}

function messageText(message: UIMessage | undefined): string {
  return (message?.parts ?? [])
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
}

async function waitForText(
  store: SubagentStreamStore,
  sessionId: string,
  expected: string
): Promise<UIMessage | undefined> {
  for (let attempt = 0; attempt < 50; attempt++) {
    const message = store.getMessage(sessionId)
    if (messageText(message) === expected) {
      return message
    }
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  return store.getMessage(sessionId)
}

describe("SubagentStreamStore", () => {
  it("builds a child message and ignores replayed chunks", async () => {
    const store = new SubagentStreamStore()
    const start = chunk(CHILD_A, "e1", 0, {
      type: "start",
      messageId: CHILD_A,
    })
    const [textStart, firstDelta] = textChunks(CHILD_A, "e2", "hello")
    const secondDelta = chunk(CHILD_A, "e3", 0, {
      type: "text-delta",
      id: "e2:0",
      delta: " world",
    })

    for (const data of [start, textStart, firstDelta]) {
      store.ingest(data)
    }
    // A reconnect replays the turn from the start before new chunks arrive.
    for (const data of [start, textStart, firstDelta, secondDelta]) {
      store.ingest(data)
    }

    const message = await waitForText(store, CHILD_A, "hello world")
    expect(message?.id).toBe(CHILD_A)
    expect(message?.role).toBe("assistant")
    expect(messageText(message)).toBe("hello world")
  })

  it("keeps child sessions isolated", async () => {
    const store = new SubagentStreamStore()
    const listener = jest.fn()
    const unsubscribe = store.subscribe(CHILD_B, listener)

    for (const data of [
      chunk(CHILD_A, "a0", 0, { type: "start", messageId: CHILD_A }),
      chunk(CHILD_B, "b0", 0, { type: "start", messageId: CHILD_B }),
      ...textChunks(CHILD_A, "a1", "alpha"),
      ...textChunks(CHILD_B, "b1", "beta"),
    ]) {
      store.ingest(data)
    }

    expect(messageText(await waitForText(store, CHILD_A, "alpha"))).toBe(
      "alpha"
    )
    expect(messageText(await waitForText(store, CHILD_B, "beta"))).toBe("beta")
    expect(listener).toHaveBeenCalled()
    unsubscribe()
  })
})

describe("parseAgentChunkData", () => {
  it("accepts the wire payload and rejects malformed data", () => {
    const valid = {
      session_id: CHILD_A,
      event_id: "e1",
      index: 0,
      chunk: { type: "start", messageId: CHILD_A },
    }
    expect(parseAgentChunkData(valid)).toEqual(valid)
    expect(parseAgentChunkData({ ...valid, index: "0" })).toBeNull()
    expect(parseAgentChunkData({ ...valid, chunk: {} })).toBeNull()
    expect(parseAgentChunkData(null)).toBeNull()
  })
})

describe("getSubagentSessionId", () => {
  it("finds the child session id in live and persisted outputs", () => {
    expect(
      getSubagentSessionId({ session_id: CHILD_A, status: "running" })
    ).toBe(CHILD_A)
    expect(getSubagentSessionId(JSON.stringify({ session_id: CHILD_A }))).toBe(
      CHILD_A
    )
    expect(
      getSubagentSessionId([
        { type: "text", text: JSON.stringify({ session_id: CHILD_A }) },
      ])
    ).toBe(CHILD_A)
    expect(
      getSubagentSessionId({ errorText: { session_id: CHILD_A, error: "x" } })
    ).toBe(CHILD_A)
    expect(getSubagentSessionId(`{'session_id': '${CHILD_A}'}`)).toBe(CHILD_A)
    expect(getSubagentSessionId({ session_id: "not-a-uuid" })).toBeNull()
    expect(getSubagentSessionId(undefined)).toBeNull()
  })
})
