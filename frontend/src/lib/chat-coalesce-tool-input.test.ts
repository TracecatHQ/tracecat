import type { UIMessageChunk } from "ai"
import { coalesceToolInputDeltas } from "@/lib/chat"

async function run(chunks: UIMessageChunk[]): Promise<UIMessageChunk[]> {
  const out: UIMessageChunk[] = []
  const stream = new ReadableStream<UIMessageChunk>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  }).pipeThrough(coalesceToolInputDeltas())
  const reader = stream.getReader()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) return out
    out.push(value)
  }
}

const delta = (toolCallId: string, inputTextDelta: string): UIMessageChunk => ({
  type: "tool-input-delta",
  toolCallId,
  inputTextDelta,
})

describe("coalesceToolInputDeltas", () => {
  it("merges consecutive deltas for one tool call", async () => {
    expect(
      await run([
        { type: "tool-input-start", toolCallId: "a", toolName: "t" },
        delta("a", '{"x":'),
        delta("a", '"y"'),
        delta("a", "}"),
        {
          type: "tool-input-available",
          toolCallId: "a",
          toolName: "t",
          input: { x: "y" },
        },
      ])
    ).toEqual([
      { type: "tool-input-start", toolCallId: "a", toolName: "t" },
      delta("a", '{"x":"y"}'),
      {
        type: "tool-input-available",
        toolCallId: "a",
        toolName: "t",
        input: { x: "y" },
      },
    ])
  })

  it("keeps order across tool calls and other chunks", async () => {
    expect(
      await run([
        delta("a", "1"),
        delta("a", "2"),
        delta("b", "3"),
        { type: "text-delta", id: "t", delta: "hi" },
        delta("b", "4"),
      ])
    ).toEqual([
      delta("a", "12"),
      delta("b", "3"),
      { type: "text-delta", id: "t", delta: "hi" },
      delta("b", "4"),
    ])
  })

  it("flushes a pending delta after the window while the stream stays open", async () => {
    jest.useFakeTimers()
    try {
      let controller!: ReadableStreamDefaultController<UIMessageChunk>
      const reader = new ReadableStream<UIMessageChunk>({
        start(c) {
          controller = c
        },
      })
        .pipeThrough(coalesceToolInputDeltas(100))
        .getReader()
      controller.enqueue(delta("a", "1"))
      const read = reader.read()
      await jest.advanceTimersByTimeAsync(100)
      await expect(read).resolves.toEqual({
        done: false,
        value: delta("a", "1"),
      })
    } finally {
      jest.useRealTimers()
    }
  })
})
