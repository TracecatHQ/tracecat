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

  it("keeps only the latest duplicate approval request", () => {
    const approval = {
      tool_call_id: "toolu_01approval",
      tool_name: "core.http_request",
      args: { url: "https://example.com" },
    }
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [{ type: "data-approval-request", data: [approval] }],
      },
      {
        id: "msg-2",
        role: "assistant",
        parts: [{ type: "data-approval-request", data: [approval] }],
      },
    ] as ai.UIMessage[]

    const transformed = transformMessages(messages)

    expect(transformed).toEqual([messages[1]])
  })

  it("deduplicates replayed pending approval parts by tool call id", () => {
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "tool-core__http_request",
            toolCallId: "toolu_01http",
            state: "input-available",
            input: { url: "https://google.com" },
          },
          {
            type: "data-approval-request",
            data: [
              {
                tool_call_id: "toolu_01http",
                tool_name: "core.http_request",
                args: { url: "https://google.com" },
              },
            ],
          },
          {
            type: "tool-core__http_request",
            toolCallId: "toolu_01http",
            state: "input-available",
            input: { url: "https://google.com" },
          },
          {
            type: "data-approval-request",
            data: [
              {
                tool_call_id: "toolu_01http",
                tool_name: "core.http_request",
                args: { url: "https://google.com" },
              },
            ],
          },
        ],
      },
    ] as ai.UIMessage[]

    const transformed = transformMessages(messages)

    expect(transformed).toHaveLength(1)
    expect(transformed[0]?.parts).toEqual([
      {
        type: "tool-core__http_request",
        toolCallId: "toolu_01http",
        state: "input-available",
        input: { url: "https://google.com" },
      },
      {
        type: "data-approval-request",
        data: [
          {
            tool_call_id: "toolu_01http",
            tool_name: "core.http_request",
            args: { url: "https://google.com" },
          },
        ],
      },
    ])
  })

  it("deduplicates replayed terminal tool parts by tool call id", () => {
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "tool-core__http_request",
            toolCallId: "toolu_01http",
            state: "input-available",
            input: { url: "https://google.com" },
          },
          {
            type: "data-approval-request",
            data: [
              {
                tool_call_id: "toolu_01http",
                tool_name: "core.http_request",
                args: { url: "https://google.com" },
              },
            ],
          },
          {
            type: "tool-core__http_request",
            toolCallId: "toolu_01http",
            state: "output-available",
            input: { url: "https://google.com" },
            output: { status_code: 200, replay: 1 },
          },
          {
            type: "tool-core__http_request",
            toolCallId: "toolu_01http",
            state: "input-available",
            input: { url: "https://google.com" },
          },
          {
            type: "data-approval-request",
            data: [
              {
                tool_call_id: "toolu_01http",
                tool_name: "core.http_request",
                args: { url: "https://google.com" },
              },
            ],
          },
          {
            type: "tool-core__http_request",
            toolCallId: "toolu_01http",
            state: "output-error",
            input: { url: "https://google.com" },
            errorText: "Connection failed",
          },
        ],
      },
    ] as ai.UIMessage[]

    const transformed = transformMessages(messages)

    expect(transformed).toHaveLength(1)
    expect(transformed[0]?.parts).toEqual([
      {
        type: "tool-core__http_request",
        toolCallId: "toolu_01http",
        state: "output-error",
        input: { url: "https://google.com" },
        errorText: "Connection failed",
      },
    ])
  })

  it.each(["input-streaming", "input-available"] as const)(
    "keeps terminal input when a replayed %s part arrives afterward",
    (state) => {
      const messages = [
        {
          id: "msg-1",
          role: "assistant",
          parts: [
            {
              type: "tool-core__http_request",
              toolCallId: "toolu_01http",
              state: "output-available",
              input: { url: "https://example.com", timeout: 30 },
              output: { status_code: 200 },
            },
            {
              type: "tool-core__http_request",
              toolCallId: "toolu_01http",
              state,
              input: { url: "https://exam" },
            },
          ],
        },
      ] as ai.UIMessage[]

      const transformed = transformMessages(messages)

      expect(transformed).toHaveLength(1)
      expect(transformed[0]?.parts).toEqual([
        {
          type: "tool-core__http_request",
          toolCallId: "toolu_01http",
          state: "output-available",
          input: { url: "https://example.com", timeout: 30 },
          output: { status_code: 200 },
        },
      ])
    }
  )
})
