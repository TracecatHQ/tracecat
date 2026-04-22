/**
 * Tests that memoized chat components do not re-render when props are unchanged.
 *
 * Verifies the React.memo wrappers on MessagePart, ToolInput, and ToolOutput
 * by counting renders via a spy on the mocked CodeBlock component.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useUpdateChat, useVercelChat } from "@/hooks/use-chat"
import { useBuilderRegistryActions } from "@/lib/hooks"

// Track how many times the CodeBlock mock renders
let codeBlockRenderCount = 0

jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { children?: React.ReactNode; code: string }) => {
    codeBlockRenderCount++
    return (
      <div data-testid="mock-code-block">
        <pre>{code}</pre>
      </div>
    )
  },
  CodeBlockCopyButton: () => null,
}))

// Mock Collapsible to always render content (Radix CollapsibleContent does not
// mount children in jsdom when closed, which makes render-count assertions vacuous).
jest.mock("@/components/ui/collapsible", () => ({
  Collapsible: ({
    children,
    className,
  }: {
    children?: React.ReactNode
    className?: string
  }) => <div className={className}>{children}</div>,
  CollapsibleTrigger: ({
    children,
    className,
  }: {
    children?: React.ReactNode
    className?: string
  }) => <div className={className}>{children}</div>,
  CollapsibleContent: ({
    children,
    className,
  }: {
    children?: React.ReactNode
    className?: string
  }) => <div className={className}>{children}</div>,
}))

jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: () => <textarea data-testid="mock-code-editor" />,
}))
jest.mock("@/hooks/use-chat", () => ({
  useVercelChat: jest.fn(),
  useGetChat: jest.fn(() => ({ chat: null })),
  useUpdateChat: jest.fn(() => ({ updateChat: jest.fn(), isUpdating: false })),
  parseChatError: (error: unknown) =>
    error instanceof Error ? error.message : "Chat error",
  makeContinueMessage: jest.fn(),
}))
jest.mock("@/lib/hooks", () => ({
  useBuilderRegistryActions: jest.fn(() => ({
    registryActions: [],
    registryActionsIsLoading: false,
  })),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

import { ToolInput, ToolOutput } from "@/components/ai-elements/tool"
// Import after mocks are set up
import { ChatSessionPane } from "@/components/chat/chat-session-pane"

const mockUseVercelChat = useVercelChat as jest.MockedFunction<
  typeof useVercelChat
>
const mockUseUpdateChat = useUpdateChat as jest.MockedFunction<
  typeof useUpdateChat
>
const mockUseBuilderRegistryActions =
  useBuilderRegistryActions as jest.MockedFunction<
    typeof useBuilderRegistryActions
  >

/**
 * Build a messages array that simulates a completed tool call.
 * Includes both input-available and output-available parts so that
 * transformMessages merges them correctly.
 */
function makeCompletedToolMessages(toolInput: unknown, toolOutput: unknown) {
  return [
    {
      id: "msg-1",
      role: "assistant",
      parts: [
        {
          type: "tool-core__http_request",
          toolCallId: "tc-1",
          state: "input-available",
          input: toolInput,
        },
        {
          type: "tool-core__http_request",
          toolCallId: "tc-1",
          state: "output-available",
          input: toolInput,
          output: toolOutput,
        },
      ],
    },
  ]
}

const chatFixture = {
  id: "chat-1",
  workspace_id: "workspace-1",
  title: "Test",
  created_by: "user-1",
  entity_type: "case" as const,
  entity_id: "case-1",
  channel_context: null,
  tools: [],
  agent_preset_id: null,
  agent_preset_version_id: null,
  harness_type: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  last_stream_id: null,
  messages: [],
}

function Wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

describe("MessagePart memoization", () => {
  beforeEach(() => {
    codeBlockRenderCount = 0
    jest.spyOn(console, "error").mockImplementation(() => undefined)
    jest.spyOn(console, "warn").mockImplementation(() => undefined)
    mockUseUpdateChat.mockReturnValue({
      updateChat: jest.fn().mockResolvedValue(undefined),
      isUpdating: false,
      updateError: null,
    })
    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions: [],
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: () => undefined,
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
    jest.clearAllMocks()
  })

  it("does not re-render completed tool calls when messages array ref changes", () => {
    // Stable objects representing the tool call's input and output.
    const toolInput = { url: "https://example.com", method: "GET" }
    const toolOutput = { status: 200, body: "OK" }

    const messages1 = makeCompletedToolMessages(toolInput, toolOutput)

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      messages: messages1,
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    const { rerender } = render(
      <Wrapper>
        <ChatSessionPane
          chat={chatFixture}
          workspaceId="workspace-1"
          entityType="case"
          entityId="case-1"
          modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
        />
      </Wrapper>
    )

    const rendersAfterMount = codeBlockRenderCount
    // Guard: CodeBlock must have rendered at least once on mount for this test to be meaningful.
    expect(rendersAfterMount).toBeGreaterThan(0)

    // Simulate what the Vercel AI SDK does on each stream chunk:
    // return a NEW messages array containing the SAME part objects.
    const messages2 = makeCompletedToolMessages(toolInput, toolOutput)

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      messages: messages2,
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    rerender(
      <Wrapper>
        <ChatSessionPane
          chat={chatFixture}
          workspaceId="workspace-1"
          entityType="case"
          entityId="case-1"
          modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
        />
      </Wrapper>
    )

    // The CodeBlock should NOT have rendered again because the tool part's
    // input/output/state are identical by reference, and MessagePart is memoized.
    expect(codeBlockRenderCount).toBe(rendersAfterMount)
  })

  it("re-renders when tool state transitions from input-available to output-available", () => {
    const toolInput = { url: "https://example.com", method: "GET" }

    // Start with only an input-available tool part
    const pendingMessages = [
      {
        id: "msg-1",
        role: "assistant",
        parts: [
          {
            type: "tool-core__http_request",
            toolCallId: "tc-1",
            state: "input-available",
            input: toolInput,
          },
        ],
      },
    ]

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      messages: pendingMessages,
      status: "streaming",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    const { rerender } = render(
      <Wrapper>
        <ChatSessionPane
          chat={chatFixture}
          workspaceId="workspace-1"
          entityType="case"
          entityId="case-1"
          modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
        />
      </Wrapper>
    )

    const rendersAfterMount = codeBlockRenderCount
    // Guard: input-available renders the input via CodeBlock
    expect(rendersAfterMount).toBeGreaterThan(0)

    // Now the tool completes — output part appended after input part
    const toolOutput = { status: 200, body: "OK" }
    const completedMessages = makeCompletedToolMessages(toolInput, toolOutput)

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      regenerate: jest.fn(),
      messages: completedMessages,
      status: "streaming",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    rerender(
      <Wrapper>
        <ChatSessionPane
          chat={chatFixture}
          workspaceId="workspace-1"
          entityType="case"
          entityId="case-1"
          modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
        />
      </Wrapper>
    )

    // The CodeBlock SHOULD have rendered again because the tool state changed
    // and the merged output-available part now also renders output
    expect(codeBlockRenderCount).toBeGreaterThan(rendersAfterMount)
  })
})

describe("ToolInput memoization", () => {
  beforeEach(() => {
    codeBlockRenderCount = 0
  })

  it("does not re-render when parent re-renders with same input ref", () => {
    const input = { url: "https://example.com", method: "GET" }

    const { rerender } = render(
      <Wrapper>
        <ToolInput input={input} />
      </Wrapper>
    )

    const rendersAfterMount = codeBlockRenderCount
    expect(rendersAfterMount).toBeGreaterThan(0)

    // Re-render with the exact same input reference
    rerender(
      <Wrapper>
        <ToolInput input={input} />
      </Wrapper>
    )

    expect(codeBlockRenderCount).toBe(rendersAfterMount)
  })

  it("re-renders when input reference changes", () => {
    const { rerender } = render(
      <Wrapper>
        <ToolInput input={{ url: "https://example.com" }} />
      </Wrapper>
    )

    const rendersAfterMount = codeBlockRenderCount
    expect(rendersAfterMount).toBeGreaterThan(0)

    // New object reference with same content — should still re-render
    // because memo uses referential equality
    rerender(
      <Wrapper>
        <ToolInput input={{ url: "https://example.com" }} />
      </Wrapper>
    )

    expect(codeBlockRenderCount).toBeGreaterThan(rendersAfterMount)
  })
})

describe("ToolOutput memoization", () => {
  beforeEach(() => {
    codeBlockRenderCount = 0
  })

  it("does not re-render when parent re-renders with same output ref", () => {
    const output = { status: 200, body: "OK" }

    const { rerender } = render(
      <Wrapper>
        <ToolOutput output={output} errorText={undefined} />
      </Wrapper>
    )

    const rendersAfterMount = codeBlockRenderCount
    expect(rendersAfterMount).toBeGreaterThan(0)

    rerender(
      <Wrapper>
        <ToolOutput output={output} errorText={undefined} />
      </Wrapper>
    )

    expect(codeBlockRenderCount).toBe(rendersAfterMount)
  })

  it("re-renders when output reference changes", () => {
    const { rerender } = render(
      <Wrapper>
        <ToolOutput output={{ status: 200 }} errorText={undefined} />
      </Wrapper>
    )

    const rendersAfterMount = codeBlockRenderCount
    expect(rendersAfterMount).toBeGreaterThan(0)

    rerender(
      <Wrapper>
        <ToolOutput output={{ status: 200 }} errorText={undefined} />
      </Wrapper>
    )

    expect(codeBlockRenderCount).toBeGreaterThan(rendersAfterMount)
  })
})
