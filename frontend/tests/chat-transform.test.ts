import type * as ai from "ai"
import { markLiveDenials, transformMessages } from "@/lib/chat"

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

  it("joins approval requests streamed back to back into one batch", () => {
    const approval = (n: number) => ({
      tool_call_id: `toolu_0${n}`,
      tool_name: "core.transform.reshape",
      args: { value: { n } },
    })
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          { type: "text", text: "Running all three." },
          { type: "data-approval-request", data: [approval(1)] },
          { type: "data-approval-request", data: [approval(2)] },
        ],
      },
      {
        id: "msg-2",
        role: "assistant",
        parts: [{ type: "data-approval-request", data: [approval(3)] }],
      },
    ] as ai.UIMessage[]

    const transformed = transformMessages(messages)

    expect(transformed).toEqual([
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          { type: "text", text: "Running all three." },
          {
            type: "data-approval-request",
            data: [approval(1), approval(2), approval(3)],
          },
        ],
      },
    ])
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

    // The card draws the waiting call, so its input part stays hidden.
    expect(transformed).toHaveLength(1)
    expect(transformed[0]?.parts).toEqual([
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

describe("markLiveDenials", () => {
  it("shows a call's edited arguments and tags denials this tab sent", () => {
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "dynamic-tool",
            toolName: "core__transform__reshape",
            toolCallId: "edited",
            state: "output-available",
            input: { value: "original" },
            output: "edited",
          },
          {
            type: "dynamic-tool",
            toolName: "core__transform__reshape",
            toolCallId: "denied",
            state: "output-error",
            input: { value: 1 },
            errorText: "denied",
          },
        ],
      },
    ] as ai.UIMessage[]

    const [message] = markLiveDenials(messages, [
      {
        tool_call_id: "edited",
        action: "override",
        override_args: { value: "edited" },
      },
      { tool_call_id: "denied", action: "deny", reason: "No" },
    ])

    expect(message.parts[0]).toMatchObject({ input: { value: "edited" } })
    expect(message.parts[1]).toMatchObject({
      callProviderMetadata: {
        tracecat: { approval: "rejected", reason: "No" },
      },
    })
  })

  it("shows a denied call as denied before its result streams in", () => {
    const messages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "dynamic-tool",
            toolName: "core__transform__reshape",
            toolCallId: "approved",
            state: "input-available",
            input: { value: 1 },
          },
          {
            type: "dynamic-tool",
            toolName: "core__transform__reshape",
            toolCallId: "denied",
            state: "input-available",
            input: { value: 2 },
          },
        ],
      },
    ] as ai.UIMessage[]

    const [message] = markLiveDenials(messages, [
      { tool_call_id: "approved", action: "approve" },
      { tool_call_id: "denied", action: "deny", reason: "No" },
    ])

    expect(message.parts[0]).toMatchObject({ state: "input-available" })
    expect(message.parts[1]).toMatchObject({
      state: "output-error",
      callProviderMetadata: {
        tracecat: { approval: "rejected", reason: "No" },
      },
    })
  })
})
