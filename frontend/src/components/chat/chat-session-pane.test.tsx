import { Chat } from "@ai-sdk/react"
import {
  act,
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

import {
  type AgentSessionsGetSessionVercelResponse,
  type UIMessage as ApiUIMessage,
  agentSessionsGetSessionVercel,
} from "@/client"

import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { useGetChatVercel } from "@/hooks/use-chat"
import type { RunningComposer, RunningComposerContext } from "@/lib/extensions"
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
  // Without a dropped connection, the hook's retry resends.
  retryTurn: jest.fn(async (messageId: string | undefined) => {
    mockUseVercelChatResult.regenerate({ messageId })
  }),
  sendMessage: jest.fn(),
  status: "ready" as ChatStatus,
}

// Tracecat installs no running composer; tests opt in through this mock.
const mockUseRunningComposer = jest.fn(
  (_context: RunningComposerContext): RunningComposer | undefined => undefined
)
jest.mock("@/extensions", () => ({
  extensions: {
    useRunningComposer: (context: RunningComposerContext) =>
      mockUseRunningComposer(context),
  },
}))

// These tests read every step of finished turns, which only the verbose level
// draws without expanding the turn first.
jest.mock("@/hooks/use-chat-verbosity", () => ({
  ...jest.requireActual("@/hooks/use-chat-verbosity"),
  useChatVerbosityPreference: () => ["verbose", jest.fn()],
}))

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

jest.mock("@/client", () => ({
  ...jest.requireActual<typeof import("@/client")>("@/client"),
  agentSessionsGetSessionVercel: jest.fn(),
}))

jest.mock("@/hooks/use-chat", () => ({
  makeContinueMessage:
    jest.requireActual<typeof import("@/hooks/use-chat")>("@/hooks/use-chat")
      .makeContinueMessage,
  useAdoptServerTranscript:
    jest.requireActual<typeof import("@/hooks/use-chat")>("@/hooks/use-chat")
      .useAdoptServerTranscript,
  useGetChatVercel:
    jest.requireActual<typeof import("@/hooks/use-chat")>("@/hooks/use-chat")
      .useGetChatVercel,
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
  useVercelChat: () => {
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
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => "hidden",
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

describe("ChatSessionPane optimistic first send", () => {
  beforeEach(() => {
    mockUseVercelChatResult.clearError.mockClear()
    mockUseVercelChatResult.regenerate.mockClear()
    mockUseVercelChatResult.sendMessage.mockClear()
    mockUseVercelChatResult.lastError = null
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "ready"
  })

  it("adopts persisted child history and disables direct messages", async () => {
    const description =
      "This subagent conversation is read-only. Message the parent conversation instead."
    const message: ApiUIMessage = {
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

  it("shows the submitted message and a thinking line before a session exists", async () => {
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
    expect(screen.getByTestId("agent-running-line")).toBeInTheDocument()
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
    expect(screen.queryByTestId("agent-running-line")).not.toBeInTheDocument()
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
    expect(screen.getByTestId("agent-running-line")).toBeInTheDocument()

    rerenderChatSessionPane(props)

    expect(screen.getByTestId("agent-running-line")).toBeInTheDocument()

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
      expect(screen.queryByTestId("agent-running-line")).not.toBeInTheDocument()
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
      data: {
        kind: "continue",
        source: "chat",
        decisions: CALLS.map((id) => ({ tool_call_id: id, action: "approve" })),
      },
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

/** A live assistant message that streamed the given compaction phases. */
function liveCompaction(
  ...phases: Array<{ phase: string; pre_tokens?: number }>
): UIMessage {
  return {
    id: "compacting-1",
    role: "assistant",
    parts: phases.map((data) => ({ type: "data-compaction", data })),
  }
}

describe("ChatSessionPane compaction badge", () => {
  const COMPACT_PROMPT: UIMessage = {
    id: "compact-prompt",
    role: "user",
    parts: [{ type: "text", text: "/compact" }],
  }

  beforeEach(() => {
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "ready"
  })

  it("shows a reloaded compaction with the tokens it compacted", () => {
    // The persisted boundary renders as a badge with its token count.
    mockUseVercelChatResult.messages = [
      COMPACT_PROMPT,
      {
        id: "compaction-1",
        role: "system",
        parts: [
          {
            type: "data-compaction",
            data: { phase: "completed", pre_tokens: 1234 },
          },
        ],
      },
    ]
    renderChatSessionPane()

    expect(screen.getByText("Compacted conversation")).toBeInTheDocument()
    expect(screen.getByText("1,234 tokens")).toBeInTheDocument()
  })

  it("shows one progress indicator for a live compaction until it completes", () => {
    // A lone started part shows the in-progress badge with its own spinner,
    // in place of the generic loader.
    mockUseVercelChatResult.status = "streaming"
    mockUseVercelChatResult.messages = [
      COMPACT_PROMPT,
      liveCompaction({ phase: "started" }),
    ]
    const { rerenderChatSessionPane } = renderChatSessionPane()

    const badge = screen.getByText("Compacting conversation").parentElement
    expect(badge?.querySelector(".animate-spin")).toBeInTheDocument()
    expect(screen.queryByTestId("agent-running-line")).not.toBeInTheDocument()
    expect(screen.queryByText("Compacted conversation")).not.toBeInTheDocument()

    // The turn keeps streaming after an automatic compaction completes, so
    // the generic loader returns.
    mockUseVercelChatResult.messages = [
      COMPACT_PROMPT,
      liveCompaction(
        { phase: "started" },
        { phase: "completed", pre_tokens: 190934 }
      ),
    ]
    rerenderChatSessionPane()

    // The result replaces the in-progress badge.
    expect(
      screen.queryByText("Compacting conversation")
    ).not.toBeInTheDocument()
    expect(screen.getByText("Compacted conversation")).toBeInTheDocument()
    expect(screen.getByText("190,934 tokens")).toBeInTheDocument()
    expect(screen.getByTestId("agent-running-line")).toBeInTheDocument()
  })

  it("shows a failed compaction in place of the in-progress badge", () => {
    // The runtime reports a compaction that never finished as failed.
    mockUseVercelChatResult.messages = [
      COMPACT_PROMPT,
      liveCompaction({ phase: "started" }, { phase: "failed" }),
    ]
    renderChatSessionPane()

    expect(screen.getByText("Compaction failed")).toBeInTheDocument()
    expect(
      screen.queryByText("Compacting conversation")
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Compacted conversation")).not.toBeInTheDocument()
  })
})

const DENIED_RESULT_TEXT =
  "Tool denied by user: Not this one\n\nDo not retry it, and wait for the user."

describe("ChatSessionPane denied calls", () => {
  beforeEach(() => {
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "ready"
  })

  it("shows a reloaded denied call as denied with the user's reason", () => {
    mockUseVercelChatResult.messages = [
      PROMPT,
      {
        id: "assistant-1",
        role: "assistant",
        parts: [
          {
            type: `tool-${TOOL}`,
            toolCallId: "first",
            state: "output-error",
            input: { operation: "first" },
            errorText: DENIED_RESULT_TEXT,
            callProviderMetadata: {
              tracecat: { approval: "rejected", reason: "Not this one" },
            },
          } as UIMessage["parts"][number],
        ],
      },
    ]
    renderChatSessionPane()

    expect(screen.getByTestId("tool-state")).toHaveTextContent("output-denied")
    expect(screen.getByText("Not this one")).toBeInTheDocument()
    // The model-facing result text is not a tool error to show.
    expect(screen.queryByTestId("tool-output")).not.toBeInTheDocument()
  })

  it("shows a call denied in this chat as denied while the turn streams", async () => {
    const denial: UIMessage = {
      id: "continue-1",
      role: "user",
      parts: [
        {
          type: "data-continue",
          data: {
            kind: "continue",
            source: "inbox",
            decisions: [
              { tool_call_id: "first", action: "deny", reason: "Not this one" },
            ],
          },
        },
      ],
    }
    const continuation = await streamed([
      { type: "start", messageId: BUBBLE },
      {
        type: "tool-input-available",
        toolCallId: "first",
        toolName: TOOL,
        input: {},
      },
      {
        type: "tool-output-error",
        toolCallId: "first",
        errorText: DENIED_RESULT_TEXT,
      },
    ])
    mockUseVercelChatResult.status = "streaming"
    mockUseVercelChatResult.messages = [
      PROMPT,
      await livePaused(),
      denial,
      continuation,
    ]
    renderChatSessionPane()

    const first = within(screen.getAllByTestId("tool")[0])
    expect(first.getByTestId("tool-input")).toHaveTextContent("first")
    expect(first.getByTestId("tool-state")).toHaveTextContent("output-denied")
    expect(first.getByText("Not this one")).toBeInTheDocument()
    expect(first.queryByTestId("tool-output")).not.toBeInTheDocument()
  })
})

const SESSION_ID = "session-1"

/** The paused turn's results and answer once an approval was decided. */
const RESOLVED: UIMessage[] = [
  PROMPT,
  {
    id: "assistant-1",
    role: "assistant",
    parts: [
      { type: "text", text: "Running three calls." },
      ...CALLS.map(storedResult),
    ],
  },
  {
    id: "answer-1",
    role: "assistant",
    parts: [{ type: "text", text: "All three calls ran." }],
  },
]

/** The session as the server returns it. */
function serverChat(
  messages: UIMessage[]
): AgentSessionsGetSessionVercelResponse {
  return {
    id: SESSION_ID,
    workspace_id: "workspace-1",
    title: "Chat",
    created_by: "user-1",
    entity_type: "copilot",
    entity_id: "entity-1",
    channel_context: null,
    tools: [],
    mcp_integrations: [],
    agent_preset_id: null,
    agent_preset_version_id: null,
    harness_type: "claude_code",
    created_at: "2026-09-25T00:00:00Z",
    updated_at: "2026-09-25T00:00:00Z",
    messages,
  } as AgentSessionsGetSessionVercelResponse
}

/** Render the pane fed by the session query, as the chat page does. */
function renderServerBackedPane() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function ServerBackedPane() {
    const { chat } = useGetChatVercel({
      chatId: SESSION_ID,
      workspaceId: "workspace-1",
    })
    return chat ? (
      <ChatSessionPane
        chat={chat}
        workspaceId="workspace-1"
        modelInfo={{ name: "gpt-test", provider: "openai" }}
        placeholder="Ask Tracecat..."
        surface="workspace-chat"
        toolsEnabled={false}
      />
    ) : null
  }
  return render(
    <QueryClientProvider client={queryClient}>
      <ServerBackedPane />
    </QueryClientProvider>
  )
}

/** The approval batch's Submit, shown in place of the prompt box. */
function approvalSubmit(): HTMLElement {
  return screen.getByRole("button", { name: "Submit" })
}

describe("ChatSessionPane approvals decided elsewhere", () => {
  const fetchSession = jest.mocked(agentSessionsGetSessionVercel)

  beforeEach(() => {
    fetchSession.mockReset()
    mockUseVercelChatResult.sendMessage.mockReset()
    mockUseVercelChatResult.status = "ready"
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it("replaces a waiting approval decided elsewhere without a reload", async () => {
    mockUseVercelChatResult.messages = [PROMPT, await livePaused()]
    jest.useFakeTimers()
    fetchSession.mockResolvedValue(serverChat([PROMPT, ...STORED_PAUSED]))
    renderServerBackedPane()
    expect(await screen.findByTestId("approval-card-first")).toBeInTheDocument()

    // Another tab approves the batch and the run finishes.
    fetchSession.mockResolvedValue(serverChat(RESOLVED))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(5_000)
    })

    await waitFor(() =>
      expect(
        screen.queryByTestId("approval-card-first")
      ).not.toBeInTheDocument()
    )
    expect(timeline()).toEqual(
      CALLS.map((id) => `${id}: output-available ({"executed":"${id}"})`)
    )
  })

  it("does not submit a stale decision for an approval decided elsewhere", async () => {
    // The tab already adopted the paused turn from the session.
    mockUseVercelChatResult.messages = [PROMPT, ...STORED_PAUSED]
    fetchSession.mockResolvedValue(serverChat([PROMPT, ...STORED_PAUSED]))
    renderServerBackedPane()
    const first = within(await screen.findByTestId("approval-card-first"))

    // The Inbox decided the batch before this tab refreshed.
    fetchSession.mockResolvedValue(serverChat(RESOLVED))
    fireEvent.click(first.getByRole("button", { name: "Approve" }))
    fireEvent.click(approvalSubmit())

    await waitFor(() =>
      expect(
        screen.queryByTestId("approval-card-first")
      ).not.toBeInTheDocument()
    )
    expect(mockUseVercelChatResult.sendMessage).not.toHaveBeenCalled()
  })

  it("refreshes a batch in which another surface decided one approval", async () => {
    /** The paused turn with each call's approval status. */
    function pausedWith(status: (id: string) => string): UIMessage[] {
      return [
        PROMPT,
        STORED_PAUSED[0],
        {
          ...STORED_PAUSED[1],
          parts: [
            {
              type: "data-approval-request",
              data: CALLS.map((id) => ({
                tool_call_id: id,
                tool_name: TOOL,
                status: status(id),
              })),
            },
          ],
        },
      ]
    }
    function approveButton(id: string): HTMLElement {
      return within(screen.getByTestId(`approval-card-${id}`)).getByRole(
        "button",
        { name: "Approve" }
      )
    }
    mockUseVercelChatResult.messages = pausedWith(() => "pending")
    jest.useFakeTimers()
    fetchSession.mockResolvedValue(serverChat(pausedWith(() => "pending")))
    renderServerBackedPane()
    await waitFor(() => expect(approveButton("first")).toBeEnabled())

    // The Inbox approves only the first call; the others still wait. Only the
    // approval payload changes, so the transcript's shape is the same.
    fetchSession.mockResolvedValue(
      serverChat(pausedWith((id) => (id === "first" ? "approved" : "pending")))
    )
    await act(async () => {
      await jest.advanceTimersByTimeAsync(5_000)
    })

    // The decided call drops its actions; the others still wait.
    await waitFor(() =>
      expect(
        within(screen.getByTestId("approval-card-first")).queryByRole(
          "button",
          { name: "Approve" }
        )
      ).not.toBeInTheDocument()
    )
    expect(approveButton("second")).toBeEnabled()
  })

  it("marks only the decisions it sent when another surface decided one", async () => {
    /** The paused turn with each call's approval status. */
    function pausedWith(status: (id: string) => string): UIMessage[] {
      return [
        PROMPT,
        STORED_PAUSED[0],
        {
          ...STORED_PAUSED[1],
          parts: [
            {
              type: "data-approval-request",
              data: CALLS.map((id) => ({
                tool_call_id: id,
                tool_name: TOOL,
                status: status(id),
              })),
            },
          ],
        },
      ]
    }
    function card(id: string) {
      return within(screen.getByTestId(`approval-card-${id}`))
    }
    mockUseVercelChatResult.messages = pausedWith(() => "pending")
    // Sending starts the continuation, as the SDK does.
    mockUseVercelChatResult.sendMessage.mockImplementation(async () => {
      mockUseVercelChatResult.status = "submitted"
    })
    fetchSession.mockResolvedValue(serverChat(pausedWith(() => "pending")))
    renderServerBackedPane()
    await waitFor(() =>
      expect(card("first").getByRole("button", { name: "Deny" })).toBeEnabled()
    )

    // This tab denies the first call and approves the second, but the Inbox
    // already approved the first.
    fireEvent.click(card("first").getByRole("button", { name: "Deny" }))
    fireEvent.click(card("second").getByRole("button", { name: "Approve" }))
    fetchSession.mockResolvedValue(
      serverChat(pausedWith((id) => (id === "first" ? "approved" : "pending")))
    )
    fireEvent.click(approvalSubmit())

    await waitFor(() =>
      expect(mockUseVercelChatResult.sendMessage).toHaveBeenCalledTimes(1)
    )
    const [sent] = mockUseVercelChatResult.sendMessage.mock.calls[0]
    expect(sent.parts[0].data.decisions).toEqual([
      { tool_call_id: "second", action: "approve" },
    ])
    // The first card never shows this tab's unsent denial as its decision.
    await waitFor(() =>
      expect(
        screen.queryByPlaceholderText("Reason, sent to the agent")
      ).not.toBeInTheDocument()
    )
    expect(
      card("second").queryByRole("button", { name: "Approve" })
    ).not.toBeInTheDocument()
  })
})

describe("ChatSessionPane running composer", () => {
  beforeEach(() => {
    mockUseRunningComposer.mockReset()
    mockUseRunningComposer.mockReturnValue(undefined)
    mockUseVercelChatResult.sendMessage.mockClear()
    mockUseVercelChatResult.messages = []
    mockUseVercelChatResult.status = "streaming"
  })

  afterAll(() => {
    mockUseVercelChatResult.status = "ready"
  })

  it("keeps the composer disabled while a turn runs without one", () => {
    renderChatSessionPane()

    expect(screen.getByPlaceholderText("Ask Tracecat...")).toBeDisabled()
    expect(mockUseRunningComposer).toHaveBeenCalledWith(
      expect.objectContaining({ running: true, capabilities: {} })
    )
  })

  it("hands the composer's text to the running turn", async () => {
    const submit = jest.fn(async () => true)
    mockUseRunningComposer.mockReturnValue({
      placeholder: "Steer the running turn...",
      submit,
      panel: <div>Steering panel</div>,
    })
    renderChatSessionPane()

    expect(screen.getByText("Steering panel")).toBeInTheDocument()
    const input = screen.getByPlaceholderText("Steer the running turn...")
    expect(input).toBeEnabled()
    fireEvent.change(input, { target: { value: "  Focus on the alert " } })
    fireEvent.keyDown(input, { key: "Enter" })

    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith("Focus on the alert")
    )
    await waitFor(() => expect(input).toHaveValue(""))
    expect(mockUseVercelChatResult.sendMessage).not.toHaveBeenCalled()
  })

  it("keeps the text when the running turn does not take it", async () => {
    const submit = jest.fn(async () => false)
    mockUseRunningComposer.mockReturnValue({
      placeholder: "Steer the running turn...",
      submit,
    })
    renderChatSessionPane()

    const input = screen.getByPlaceholderText("Steer the running turn...")
    fireEvent.change(input, { target: { value: "Focus on the alert" } })
    fireEvent.keyDown(input, { key: "Enter" })

    await waitFor(() => expect(submit).toHaveBeenCalled())
    expect(input).toHaveValue("Focus on the alert")
  })

  it("keeps a draft with attachments out of the running turn", async () => {
    const submit = jest.fn(async () => true)
    mockUseRunningComposer.mockReturnValue({
      placeholder: "Steer the running turn...",
      submit,
    })
    const createObjectURL = jest.fn(() => "blob:shot")
    Object.assign(URL, { createObjectURL, revokeObjectURL: jest.fn() })
    renderChatSessionPane()

    const input = screen.getByPlaceholderText("Steer the running turn...")
    const shot = new File(["png"], "shot.png", { type: "image/png" })
    fireEvent.paste(input, {
      clipboardData: { items: [{ kind: "file", getAsFile: () => shot }] },
    })
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    fireEvent.change(input, { target: { value: "Use this screenshot" } })
    fireEvent.keyDown(input, { key: "Enter" })

    await act(async () => undefined)
    expect(submit).not.toHaveBeenCalled()
    expect(input).toHaveValue("Use this screenshot")
  })

  it("leaves an idle composer to the normal send", async () => {
    const submit = jest.fn(async () => true)
    mockUseRunningComposer.mockReturnValue({
      placeholder: "Steer the running turn...",
      submit,
    })
    mockUseVercelChatResult.status = "ready"
    renderChatSessionPane({ chat: serverChat([]) })

    const input = screen.getByPlaceholderText("Ask Tracecat...")
    fireEvent.change(input, { target: { value: "Hello" } })
    fireEvent.keyDown(input, { key: "Enter" })

    await waitFor(() =>
      expect(mockUseVercelChatResult.sendMessage).toHaveBeenCalledTimes(1)
    )
    expect(submit).not.toHaveBeenCalled()
  })

  it("shows a user message that joined the turn where it joined", async () => {
    mockUseVercelChatResult.messages = [
      { id: "user-1", role: "user", parts: [{ type: "text", text: "Start" }] },
      {
        id: "assistant-1",
        role: "assistant",
        parts: [
          { type: "text", text: "Working on it" },
          {
            type: "data-user-message",
            data: { id: "steer-1", text: "Focus on the alert" },
          },
        ],
      },
    ]
    renderChatSessionPane()

    const joined = await screen.findByText("Focus on the alert")
    // Where it joined: after the text before it.
    expect(
      screen.getByText("Working on it").compareDocumentPosition(joined) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })
})
