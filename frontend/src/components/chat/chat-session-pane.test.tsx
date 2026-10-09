import { Chat } from "@ai-sdk/react"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import {
  type ChatStatus,
  readUIMessageStream,
  type UIMessage,
  type UIMessageChunk,
} from "ai"
import type { ReactNode } from "react"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { QueryClient, QueryClientProvider } from "@/lib/query"

// jsdom lacks structuredClone, which readUIMessageStream uses per update.
if (typeof globalThis.structuredClone !== "function") {
  globalThis.structuredClone = ((value: unknown) =>
    JSON.parse(JSON.stringify(value))) as typeof structuredClone
}

const mockUseVercelChatResult = {
  clearError: jest.fn(),
  lastError: null as string | null,
  messages: [] as UIMessage[],
  regenerate: jest.fn(),
  sendMessage: jest.fn(),
  status: "ready" as ChatStatus,
}
const mockUseVercelChatOptions = jest.fn()

jest.mock("@/components/chat/chat-empty-hero", () => ({
  ChatEmptyHero: ({ children }: { children: ReactNode }) => (
    <div data-testid="chat-empty-hero">{children}</div>
  ),
}))

jest.mock("@/components/icons", () => ({
  getIcon: () => null,
  ProviderIcon: () => <span data-testid="provider-icon" />,
}))

jest.mock("@/components/ai-elements/tool", () => ({
  getStatusBadge: () => null,
  Tool: ({ children }: { children?: ReactNode }) => (
    <div data-testid="tool">{children}</div>
  ),
  ToolContent: ({ children }: { children?: ReactNode }) => (
    <div>{children}</div>
  ),
  ToolHeader: ({ state }: { state: string }) => (
    <div data-testid="tool-state">{state}</div>
  ),
  ToolInput: ({ input }: { input?: { operation?: string } }) => (
    <div data-testid="tool-input">{input?.operation}</div>
  ),
  ToolOutput: ({
    errorText,
    output,
  }: {
    errorText?: string
    output?: unknown
  }) => <div data-testid="tool-output">{errorText ?? String(output)}</div>,
}))

jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: ({ value }: { value?: string }) => <pre>{value}</pre>,
}))

jest.mock("@/components/json-viewer", () => ({
  JsonViewWithControls: ({ data }: { data?: unknown }) => (
    <pre>{JSON.stringify(data)}</pre>
  ),
}))

jest.mock("@/hooks/use-chat", () => ({
  makeContinueMessage: jest.fn(),
  useAdoptServerTranscript:
    jest.requireActual<typeof import("@/hooks/use-chat")>("@/hooks/use-chat")
      .useAdoptServerTranscript,
  parseChatError: (error: unknown) =>
    error instanceof Error ? error.message : "Chat error",
  useUpdateChat: () => ({
    isUpdating: false,
    updateChat: jest.fn(),
  }),
  useCancelChatTurn: () => ({
    cancelChatTurn: jest.fn(),
    isCancellingChatTurn: false,
  }),
  useVercelChat: (options: { chatId?: string; resume?: boolean }) => {
    mockUseVercelChatOptions(options)
    const { useState } = jest.requireActual<typeof import("react")>("react")
    const [adoptedMessages, setMessages] = useState<UIMessage[] | null>(null)
    return {
      ...mockUseVercelChatResult,
      messages: adoptedMessages ?? mockUseVercelChatResult.messages,
      setMessages,
    }
  },
}))

jest.mock("@/lib/hooks", () => ({
  useBuilderRegistryActions: () => ({
    registryActions: [],
    registryActionsIsLoading: false,
  }),
  useListMcpIntegrations: () => ({
    mcpIntegrations: [],
    mcpIntegrationsIsLoading: false,
    mcpIntegrationsError: null,
  }),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(() => true),
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(() => ({
    hasEntitlement: () => false,
    isLoading: false,
    hasEntitlementData: true,
  })),
}))
jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: jest.fn(() => ({
    presets: [],
    presetsIsLoading: false,
    presetsError: null,
    refetchPresets: jest.fn(),
  })),
}))

function renderChatSessionPane(
  props: Partial<Parameters<typeof ChatSessionPane>[0]> = {}
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })

  function renderPane(nextProps = props) {
    return (
      <QueryClientProvider client={queryClient}>
        <ChatSessionPane
          workspaceId="workspace-1"
          modelInfo={{ name: "gpt-test", provider: "openai" }}
          placeholder="Ask Tracecat..."
          inputDisabledPlaceholder="Creating chat..."
          surface="workspace-chat"
          toolsEnabled={false}
          {...nextProps}
        />
      </QueryClientProvider>
    )
  }

  const view = render(renderPane())
  return {
    ...view,
    rerenderChatSessionPane: (nextProps = props) =>
      view.rerender(renderPane(nextProps)),
  }
}

function createChatSession(
  id: string
): NonNullable<Parameters<typeof ChatSessionPane>[0]["chat"]> {
  return {
    id,
    workspace_id: "workspace-1",
    title: "Synthetic chat",
    created_by: "user-1",
    entity_type: "agent_preset",
    entity_id: "preset-1",
    channel_context: null,
    tools: [],
    mcp_integrations: [],
    agent_preset_id: "preset-1",
    agent_preset_version_id: null,
    harness_type: "pi",
    created_at: "2026-09-25T00:00:00Z",
    updated_at: "2026-09-25T00:00:00Z",
    messages: [],
  }
}

describe("ChatSessionPane optimistic first send", () => {
  beforeEach(() => {
    mockUseVercelChatResult.clearError.mockClear()
    mockUseVercelChatResult.regenerate.mockClear()
    mockUseVercelChatResult.sendMessage.mockReset()
    mockUseVercelChatOptions.mockClear()
    mockUseVercelChatResult.lastError = null
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "ready"
  })

  it("does not resume a new session while its first prompt is in flight", async () => {
    const chat = createChatSession("new-session")
    const onPendingMessageSent = jest.fn()
    mockUseVercelChatResult.sendMessage.mockImplementationOnce(
      () => new Promise<void>(() => undefined)
    )
    const { rerenderChatSessionPane } = renderChatSessionPane({
      chat,
      pendingMessage: "First prompt",
      onPendingMessageSent,
    })

    await waitFor(() =>
      expect(mockUseVercelChatResult.sendMessage).toHaveBeenCalledWith({
        text: "First prompt",
      })
    )
    expect(onPendingMessageSent).toHaveBeenCalledTimes(1)
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "new-session", resume: false })
    )

    rerenderChatSessionPane({ chat, onPendingMessageSent })

    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "new-session", resume: false })
    )
    expect(mockUseVercelChatResult.sendMessage).toHaveBeenCalledTimes(1)
  })

  it("resets reconnect eligibility when switching between existing and forked sessions", () => {
    const existingChat = createChatSession("existing-session")
    const forkedChat = createChatSession("forked-session")
    const { rerenderChatSessionPane } = renderChatSessionPane({
      chat: existingChat,
    })
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "existing-session", resume: true })
    )

    rerenderChatSessionPane({
      chat: forkedChat,
      pendingMessage: "Forked prompt",
    })
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "forked-session", resume: false })
    )

    rerenderChatSessionPane({ chat: forkedChat })
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "forked-session", resume: false })
    )

    rerenderChatSessionPane({ chat: existingChat })
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "existing-session", resume: true })
    )
  })

  it("keeps reconnect disabled for terminal sessions", () => {
    renderChatSessionPane({
      chat: createChatSession("terminal-session"),
      resume: false,
    })
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "terminal-session", resume: false })
    )
    expect(mockUseVercelChatResult.sendMessage).not.toHaveBeenCalled()
  })

  it("honors reconnect becoming enabled for the same existing session", () => {
    const chat = createChatSession("approval-session")
    const { rerenderChatSessionPane } = renderChatSessionPane({
      chat,
      resume: false,
    })
    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "approval-session", resume: false })
    )

    rerenderChatSessionPane({ chat, resume: true })

    expect(mockUseVercelChatOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ chatId: "approval-session", resume: true })
    )
    expect(mockUseVercelChatResult.sendMessage).not.toHaveBeenCalled()
  })

  it("adopts persisted child history and disables direct messages", async () => {
    const description =
      "This subagent conversation is read-only. Message the parent conversation instead."
    const message: UIMessage = {
      id: "child-message",
      role: "assistant",
      parts: [{ type: "text", text: "Child result" }],
    }
    renderChatSessionPane({
      chat: {
        id: "child-session",
        workspace_id: "workspace-1",
        title: "Child",
        created_by: "user-1",
        is_readonly: true,
        spawned_by_session_id: "parent-session",
        entity_type: "agent_preset",
        entity_id: "preset-1",
        channel_context: null,
        tools: [],
        mcp_integrations: [],
        agent_preset_id: "preset-1",
        agent_preset_version_id: null,
        harness_type: "pi",
        created_at: "2026-09-25T00:00:00Z",
        updated_at: "2026-09-25T00:00:00Z",
        messages: [message],
      },
    })
    expect(await screen.findByText("Child result")).toBeInTheDocument()
    expect(screen.getByText(description)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(description)).toBeDisabled()
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled()
    expect(mockUseVercelChatResult.sendMessage).not.toHaveBeenCalled()
  })

  it("shows the submitted message and loading dots before a session exists", async () => {
    const onBeforeSend = jest.fn(
      () => new Promise<string | null>(() => undefined)
    )

    renderChatSessionPane({
      onBeforeSend,
      optimisticBeforeSend: true,
    })

    const input = screen.getByPlaceholderText("Ask Tracecat...")
    fireEvent.change(input, {
      target: { value: "Summarize this workspace" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Submit" }))

    await waitFor(() =>
      expect(onBeforeSend).toHaveBeenCalledWith(
        "Summarize this workspace",
        [],
        []
      )
    )

    expect(
      await screen.findByText("Summarize this workspace")
    ).toBeInTheDocument()
    expect(screen.getByTestId("dots-loader")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Creating chat...")).toBeDisabled()
  })

  it("restores the draft when the session creation is cancelled", async () => {
    const onBeforeSend = jest.fn(async () => null)

    renderChatSessionPane({
      onBeforeSend,
      optimisticBeforeSend: true,
    })

    const input = screen.getByPlaceholderText("Ask Tracecat...")
    fireEvent.change(input, {
      target: { value: "Try again" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Submit" }))

    await waitFor(() => expect(input).not.toBeDisabled())
    expect(input).toHaveValue("Try again")
    expect(screen.queryByTestId("dots-loader")).not.toBeInTheDocument()
  })

  it("keeps the optimistic message when prior history has matching text", async () => {
    const onBeforeSend = jest.fn(
      () => new Promise<string | null>(() => undefined)
    )
    const priorMessage: UIMessage = {
      id: "message-old",
      role: "user",
      parts: [{ type: "text", text: "Repeat this" }],
    }
    mockUseVercelChatResult.messages = [priorMessage]

    const props = {
      onBeforeSend,
      optimisticBeforeSend: true,
    }
    const { rerenderChatSessionPane } = renderChatSessionPane(props)

    const input = screen.getByPlaceholderText("Ask Tracecat...")
    fireEvent.change(input, {
      target: { value: "Repeat this" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Submit" }))

    await waitFor(() => expect(onBeforeSend).toHaveBeenCalled())
    expect(screen.getByTestId("dots-loader")).toBeInTheDocument()

    rerenderChatSessionPane(props)

    expect(screen.getByTestId("dots-loader")).toBeInTheDocument()

    mockUseVercelChatResult.messages = [
      priorMessage,
      {
        id: "message-new",
        role: "user",
        parts: [{ type: "text", text: "Repeat this" }],
      },
    ]
    rerenderChatSessionPane(props)

    await waitFor(() =>
      expect(screen.queryByTestId("dots-loader")).not.toBeInTheDocument()
    )
  })
})

const BUBBLE = "session-1:turn-1"
const TOOL = "mcp__tracecat__core__workflow__execute"
const CALLS = ["first", "second", "third"]
const PROMPT: UIMessage = {
  id: "prompt",
  role: "user",
  parts: [{ type: "text", text: "Run three calls" }],
}
// The approval submission, which shows nothing.
const SUBMITTED: UIMessage = {
  id: "continue-1",
  role: "user",
  parts: [
    {
      type: "data-continue",
      data: { kind: "continue", source: "chat", decisions: [] },
    },
  ],
}

/** Run Vercel UI chunks through the AI SDK, as useChat does for one request. */
async function streamed(chunks: UIMessageChunk[]): Promise<UIMessage> {
  let message: UIMessage | undefined
  for await (const next of readUIMessageStream({
    stream: new ReadableStream<UIMessageChunk>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(chunk)
        }
        controller.close()
      },
    }),
  })) {
    message = next
  }
  if (!message) {
    throw new Error("The stream produced no message")
  }
  return message
}

/** The turn as it streamed live up to its approval pause. */
function livePaused(): Promise<UIMessage> {
  return streamed([
    { type: "start", messageId: BUBBLE },
    { type: "text-start", id: "text-1" },
    { type: "text-delta", id: "text-1", delta: "Running three calls." },
    { type: "text-end", id: "text-1" },
    ...CALLS.map(
      (id): UIMessageChunk => ({
        type: "tool-input-available",
        toolCallId: id,
        toolName: TOOL,
        input: { operation: id },
      })
    ),
    {
      type: "data-approval-request",
      data: CALLS.map((id) => ({ tool_call_id: id, tool_name: TOOL })),
    },
    { type: "finish" },
  ])
}

/**
 * The live transcript of an approved batch the user stopped: the paused turn,
 * the approval submission, then the continuation's bubble. Tracecat streams the
 * continuation under the paused turn's bubble id and opens each late result
 * with its tool input, as its Vercel adapter does.
 */
async function liveStoppedBatch(
  results: Record<string, unknown>
): Promise<UIMessage[]> {
  const paused = await livePaused()
  const continuation = await streamed([
    { type: "start", messageId: BUBBLE },
    // The cancel arrives while the first call is still running.
    {
      type: "data-cancelled",
      data: { reason: "user_cancel", tool_call_ids: [] },
    },
    ...Object.entries(results).flatMap(([id, output]): UIMessageChunk[] => [
      {
        type: "tool-input-available",
        toolCallId: id,
        toolName: TOOL,
        input: {},
      },
      { type: "tool-output-available", toolCallId: id, output },
    ]),
  ])
  return [PROMPT, paused, SUBMITTED, continuation]
}

/**
 * Tool cards and turn markers in display order. A card reads
 * `operation: state (output)`, with the shown output or interruption notice.
 */
function timeline(): string[] {
  const cards = screen.queryAllByTestId("tool").map((element) => {
    const card = within(element)
    const shown =
      card.queryByTestId("tool-output")?.textContent ??
      card.queryByText("Stopped before completion")?.textContent
    const operation = card.getByTestId("tool-input").textContent
    const state = card.getByTestId("tool-state").textContent
    return { element, label: `${operation}: ${state} (${shown})` }
  })
  const markers = screen
    .queryAllByText("Interrupted", { exact: true })
    .map((element) => ({ element, label: "Interrupted" }))
  return [...cards, ...markers]
    .sort((a, b) =>
      a.element.compareDocumentPosition(b.element) &
      Node.DOCUMENT_POSITION_FOLLOWING
        ? -1
        : 1
    )
    .map(({ label }) => label)
}

describe("ChatSessionPane stopped approved calls", () => {
  beforeEach(() => {
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "ready"
  })

  it("replaces Interrupted with a result that arrives after the cancel", async () => {
    mockUseVercelChatResult.status = "streaming"
    mockUseVercelChatResult.messages = await liveStoppedBatch({})
    const { rerenderChatSessionPane } = renderChatSessionPane()

    // The cancel shows at once, while the first call is still running.
    expect(timeline()).toEqual([
      "first: output-interrupted (Stopped before completion)",
      "second: output-interrupted (Stopped before completion)",
      "third: output-interrupted (Stopped before completion)",
      "Interrupted",
    ])

    mockUseVercelChatResult.status = "ready"
    mockUseVercelChatResult.messages = await liveStoppedBatch({
      first: '{"executed":"first"}',
    })
    rerenderChatSessionPane()

    // The call in flight finished with its real result; the turn's single
    // marker still closes the turn.
    expect(timeline()).toEqual([
      'first: output-available ({"executed":"first"})',
      "second: output-interrupted (Stopped before completion)",
      "third: output-interrupted (Stopped before completion)",
      "Interrupted",
    ])
  })

  it("shows recorded results for calls that never started", async () => {
    mockUseVercelChatResult.messages = await liveStoppedBatch({
      first: '{"executed":"first"}',
      second: { errorText: "Tool call cancelled" },
      third: { errorText: "Tool call cancelled" },
    })
    renderChatSessionPane()

    expect(timeline()).toEqual([
      'first: output-available ({"executed":"first"})',
      "second: output-error (Tool call cancelled)",
      "third: output-error (Tool call cancelled)",
      "Interrupted",
    ])
  })

  it("keeps calls without a result interrupted when the turn ends", async () => {
    mockUseVercelChatResult.messages = await liveStoppedBatch({})
    renderChatSessionPane()

    expect(timeline()).toEqual([
      "first: output-interrupted (Stopped before completion)",
      "second: output-interrupted (Stopped before completion)",
      "third: output-interrupted (Stopped before completion)",
      "Interrupted",
    ])
  })

  it("shows reloaded history with the marker after the turn", () => {
    mockUseVercelChatResult.messages = [
      PROMPT,
      {
        id: "assistant-1",
        role: "assistant",
        parts: [
          { type: "text", text: "Running three calls." },
          {
            type: `tool-${TOOL}`,
            toolCallId: "first",
            state: "output-available",
            input: { operation: "first" },
            output: '{"executed":"first"}',
          },
          ...["second", "third"].map(
            (id) =>
              ({
                type: `tool-${TOOL}`,
                toolCallId: id,
                state: "output-error",
                input: { operation: id },
                errorText: "Tool call cancelled",
              }) as UIMessage["parts"][number]
          ),
        ],
      } as UIMessage,
      {
        id: "cancelled-1",
        role: "system",
        parts: [
          {
            type: "data-cancelled",
            data: { reason: "user_cancel", tool_call_ids: [] },
          },
        ],
      },
    ]
    renderChatSessionPane()

    expect(timeline()).toEqual([
      'first: output-available ({"executed":"first"})',
      "second: output-error (Tool call cancelled)",
      "third: output-error (Tool call cancelled)",
      "Interrupted",
    ])
  })
})

/**
 * Back the pane's Retry with the AI SDK's own regenerate. Returns the message
 * each request carries: Tracecat's transport sends the request's last one.
 */
function retryRequests(messages: UIMessage[]): UIMessage[] {
  const sent: UIMessage[] = []
  const chat = new Chat({
    messages,
    transport: {
      sendMessages: async ({ messages: request }) => {
        sent.push(request[request.length - 1])
        return new ReadableStream<UIMessageChunk>({
          start: (controller) => controller.close(),
        })
      },
      reconnectToStream: async () => null,
    },
  })
  mockUseVercelChatResult.regenerate.mockImplementation(chat.regenerate)
  return sent
}

// The paused turn as the database returns it, with ids of its own.
const STORED_PAUSED: UIMessage[] = [
  {
    id: "assistant-1",
    role: "assistant",
    parts: [
      { type: "text", text: "Running three calls." },
      ...CALLS.map(
        (id) =>
          ({
            type: `tool-${TOOL}`,
            toolCallId: id,
            state: "input-available",
            input: { operation: id },
          }) as UIMessage["parts"][number]
      ),
    ],
  },
  {
    id: "approval-1",
    role: "assistant",
    parts: [
      {
        type: "data-approval-request",
        data: CALLS.map((id) => ({ tool_call_id: id, tool_name: TOOL })),
      },
    ],
  },
]

// The compaction boundary the database keeps as a system record.
const COMPACTED: UIMessage = {
  id: "compaction-1",
  role: "system",
  parts: [{ type: "data-compaction", data: { phase: "completed" } }],
}

/** A completed call as the database returns it. */
function storedResult(id: string): UIMessage["parts"][number] {
  return {
    type: `tool-${TOOL}`,
    toolCallId: id,
    state: "output-available",
    input: { operation: id },
    output: `{"executed":"${id}"}`,
  } as UIMessage["parts"][number]
}

describe("ChatSessionPane response actions", () => {
  beforeEach(() => {
    mockUseVercelChatResult.regenerate.mockReset()
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "ready"
  })

  it.each([
    ["from the database", async () => STORED_PAUSED],
    ["streamed live, sharing the bubble id", async () => [await livePaused()]],
  ])(
    "retries an output-only continuation that fails before any text, with the paused turn %s",
    async (_, paused) => {
      // The continuation streams only results under the turn's bubble id, then
      // fails.
      const continuation = await streamed([
        { type: "start", messageId: BUBBLE },
        ...CALLS.flatMap((id): UIMessageChunk[] => [
          {
            type: "tool-input-available",
            toolCallId: id,
            toolName: TOOL,
            input: {},
          },
          {
            type: "tool-output-available",
            toolCallId: id,
            output: `{"executed":"${id}"}`,
          },
        ]),
        { type: "error", errorText: "Model request failed" },
      ])
      mockUseVercelChatResult.status = "error"
      mockUseVercelChatResult.messages = [
        PROMPT,
        ...(await paused()),
        SUBMITTED,
        continuation,
      ]
      const requests = retryRequests(mockUseVercelChatResult.messages)
      renderChatSessionPane()

      // The results fill the paused turn's cards, so the continuation shows
      // nothing of its own and the paused turn carries the response actions.
      expect(timeline()).toEqual(
        CALLS.map((id) => `${id}: output-available ({"executed":"${id}"})`)
      )
      const retry = screen.getAllByRole("button", { name: "Retry" })
      expect(retry).toHaveLength(1)

      // Retrying resubmits the approvals rather than the original prompt.
      fireEvent.click(retry[0])
      await waitFor(() => expect(requests).toEqual([SUBMITTED]))
    }
  )

  it("retries a reloaded turn that compacted then failed from its prompt", async () => {
    // The turn answered, compacted, then failed before another message. The
    // database keeps the compaction boundary as a trailing system record.
    mockUseVercelChatResult.messages = [
      PROMPT,
      {
        id: "answer-1",
        role: "assistant",
        parts: [{ type: "text", text: "Here is what I found so far." }],
      },
      COMPACTED,
    ]
    const requests = retryRequests(mockUseVercelChatResult.messages)
    renderChatSessionPane()

    const retry = screen.getAllByRole("button", { name: "Retry" })
    expect(retry).toHaveLength(1)
    fireEvent.click(retry[0])

    expect(mockUseVercelChatResult.regenerate).toHaveBeenCalledWith({
      messageId: PROMPT.id,
    })
    await waitFor(() => expect(requests).toEqual([PROMPT]))
  })

  it.each([
    ["", []],
    [" then compacted", [COMPACTED]],
  ])(
    "retries a reloaded turn with several assistant records%s from its prompt",
    async (_, trailing) => {
      // The database splits a tool-using turn into one assistant record per
      // model response: narration with a tool call, a tool call alone, then
      // the answer.
      mockUseVercelChatResult.messages = [
        PROMPT,
        {
          id: "assistant-1",
          role: "assistant",
          parts: [
            { type: "text", text: "Checking the first call." },
            storedResult("first"),
          ],
        },
        {
          id: "assistant-2",
          role: "assistant",
          parts: [storedResult("second")],
        },
        {
          id: "answer-1",
          role: "assistant",
          parts: [{ type: "text", text: "Both calls ran." }],
        },
        ...trailing,
      ]
      const requests = retryRequests(mockUseVercelChatResult.messages)
      renderChatSessionPane()

      const retry = screen.getAllByRole("button", { name: "Retry" })
      expect(retry).toHaveLength(1)
      fireEvent.click(retry[0])

      // The request resends the prompt, not an earlier record of the turn.
      await waitFor(() => expect(requests).toEqual([PROMPT]))
    }
  )
})
