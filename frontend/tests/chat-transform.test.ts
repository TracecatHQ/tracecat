import type * as ai from "ai"
import { transformMessages } from "@/lib/chat"

describe("transformMessages", () => {
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
