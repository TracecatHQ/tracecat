import type * as ai from "ai"
import { transformMessages } from "@/lib/chat"

describe("transformMessages", () => {
  it("keeps completed tool parts that already contain their input", () => {
    const warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {})
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "tool-run_query",
            toolCallId: "toolu_01test",
            state: "output-available",
            input: { query: "select 1" },
            output: { rows: [] },
          },
        ],
      },
    ] as ai.UIMessage[]

    const transformed = transformMessages(messages)

    expect(transformed).toEqual(messages)
    expect(warnSpy).not.toHaveBeenCalled()
    warnSpy.mockRestore()
  })

  it("clears a pending compaction badge when a failed event arrives", () => {
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "data-compaction",
            data: { phase: "started" },
          },
        ],
      },
      {
        id: "msg-2",
        role: "assistant",
        parts: [
          {
            type: "data-compaction",
            data: { phase: "failed" },
          },
        ],
      },
    ] as ai.UIMessage[]

    const transformed = transformMessages(messages)

    expect(transformed).toHaveLength(1)
    expect(transformed[0]?.parts).toEqual([
      {
        type: "data-compaction",
        data: { phase: "failed" },
      },
    ])
  })
})
