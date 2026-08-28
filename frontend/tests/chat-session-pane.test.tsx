import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import type { UIMessage } from "ai"
import { StrictMode } from "react"
import type {
  AgentPresetReadMinimal,
  AgentSessionReadVercel,
  MCPIntegrationRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  ChatSessionPane,
  MessagePart,
} from "@/components/chat/chat-session-pane"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useUpdateChat, useVercelChat } from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useBuilderRegistryActions, useListMcpIntegrations } from "@/lib/hooks"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/hooks/use-chat", () => ({
  useVercelChat: jest.fn(),
  useAdoptServerTranscript:
    jest.requireActual("@/hooks/use-chat").useAdoptServerTranscript,
  useGetChat: jest.fn(() => ({ chat: null })),
  useUpdateChat: jest.fn(() => ({ updateChat: jest.fn(), isUpdating: false })),
  useCancelChatTurn: jest.fn(() => ({
    cancelChatTurn: jest.fn(),
    isCancellingChatTurn: false,
  })),
  parseChatError: (error: unknown) =>
    error instanceof Error ? error.message : "Chat error",
  makeContinueMessage: (decisions: unknown) => ({
    id: "continue-test",
    role: "user",
    parts: [
      {
        type: "data-continue",
        data: { format: "continue", decisions },
      },
    ],
  }),
}))
jest.mock("@/lib/hooks", () => ({
  useBuilderRegistryActions: jest.fn(() => ({
    registryActions: [],
    registryActionsIsLoading: false,
  })),
  useListMcpIntegrations: jest.fn(() => ({
    mcpIntegrations: [],
    mcpIntegrationsIsLoading: false,
    mcpIntegrationsError: null,
  })),
}))
jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({
    children,
    code,
  }: {
    children?: React.ReactNode
    code: string
  }) => (
    <div data-testid="mock-code-block">
      {children}
      <pre>{code}</pre>
    </div>
  ),
  CodeBlockCopyButton: () => null,
}))
jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: ({
    value,
    onChange,
    className,
  }: {
    value: string
    onChange?: (value: string) => void
    className?: string
  }) => (
    <textarea
      data-testid="mock-code-editor"
      className={className}
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { firstName: "Daryl" } }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(),
}))
jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: jest.fn(),
}))

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
const mockUseListMcpIntegrations =
  useListMcpIntegrations as jest.MockedFunction<typeof useListMcpIntegrations>
const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>
const mockUseEntitlements = useEntitlements as jest.MockedFunction<
  typeof useEntitlements
>
const mockUseAgentPresets = useAgentPresets as jest.MockedFunction<
  typeof useAgentPresets
>

const TRIAGE_PRESET: AgentPresetReadMinimal = {
  id: "preset-1",
  name: "Triage agent",
  description: "Triages new cases",
  current_version_id: "version-1",
  created_at: "2024-01-01T00:00:00.000Z",
  updated_at: "2024-01-01T00:00:00.000Z",
} as AgentPresetReadMinimal

const MALWARE_PRESET: AgentPresetReadMinimal = {
  id: "preset-2",
  name: "Malware agent",
  description: "Analyses malware",
  current_version_id: "version-2",
  created_at: "2024-01-01T00:00:00.000Z",
  updated_at: "2024-01-01T00:00:00.000Z",
} as AgentPresetReadMinimal

/** Grant or withhold every entitlement the mention layer looks at. */
function mockEntitled(entitled: boolean) {
  mockUseEntitlements.mockReturnValue({
    hasEntitlement: () => entitled,
    isLoading: false,
    hasEntitlementData: true,
  })
}

/** Serve the agent-mention popover a fixed preset list. */
function mockPresets(presets: AgentPresetReadMinimal[]) {
  mockUseAgentPresets.mockReturnValue({
    presets,
    presetsIsLoading: false,
    presetsError: null,
    refetchPresets: jest.fn(),
    // biome-ignore lint/suspicious/noExplicitAny: partial mock of a query result
  } as any)
}

/** Serve the agent-mention popover a failed preset lookup. */
function mockPresetsError() {
  mockUseAgentPresets.mockReturnValue({
    presets: undefined,
    presetsIsLoading: false,
    presetsError: new Error("boom"),
    refetchPresets: jest.fn(),
    // biome-ignore lint/suspicious/noExplicitAny: partial mock of a query result
  } as any)
}

/** Add a registry tool through the Tools picker, then close the popover. */
async function addToolFromPicker(label: string) {
  fireEvent.click(screen.getByRole("button", { name: /^Tools/ }))
  const search = await screen.findByPlaceholderText(
    "Search capabilities & tools..."
  )
  fireEvent.change(search, { target: { value: label } })
  const row = (await screen.findByText(label)).closest("label")
  if (!row) {
    throw new Error(`Tool row for "${label}" should exist`)
  }
  fireEvent.click(within(row).getByRole("switch"))
  fireEvent.keyDown(search, { key: "Escape" })
  await waitFor(() =>
    expect(
      screen.queryByPlaceholderText("Search capabilities & tools...")
    ).not.toBeInTheDocument()
  )
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const createChatFixture = (
  overrides?: Partial<AgentSessionReadVercel>
): AgentSessionReadVercel => ({
  id: "chat-1",
  workspace_id: "workspace-1",
  title: "Test Chat",
  created_by: "user-1",
  entity_type: "case",
  entity_id: "case-1",
  channel_context: null,
  tools: [],
  mcp_integrations: [],
  agent_preset_id: null,
  agents_binding: null,
  harness_type: null,
  created_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
  updated_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
  last_stream_id: null,
  messages: [],
  ...overrides,
  agent_preset_version_id: overrides?.agent_preset_version_id ?? null,
})

const createMcpIntegrationFixture = (
  overrides?: Partial<MCPIntegrationRead>
): MCPIntegrationRead => ({
  id: "mcp-1",
  workspace_id: "workspace-1",
  name: "RunReveal",
  description: "RunReveal MCP",
  slug: "runreveal",
  server_type: "http",
  server_uri: "https://mcp.example.test",
  auth_type: "OAUTH2",
  oauth_integration_id: null,
  stdio_command: null,
  stdio_args: null,
  has_stdio_env: false,
  timeout: null,
  created_at: "2024-01-01T00:00:00.000Z",
  updated_at: "2024-01-01T00:00:00.000Z",
  ...overrides,
  state: overrides?.state ?? "connected",
})

describe("ChatSessionPane", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => undefined)
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
    mockUseListMcpIntegrations.mockReturnValue({
      mcpIntegrations: [],
      mcpIntegrationsIsLoading: false,
      mcpIntegrationsError: null,
    })
    mockUseScopeCheck.mockReturnValue(true)
    mockEntitled(true)
    mockPresets([])
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
    jest.clearAllMocks()
  })

  it("labels Agent tool calls with the invoked subagent type", () => {
    const agentToolPart = {
      type: "tool-Agent",
      toolCallId: "tc-agent-1",
      state: "output-available",
      input: {
        args: {
          subagent_type: "case-management",
          description: "List all Tracecat cases",
          prompt: "Please list all the cases we currently have.",
        },
      },
      output: { cases: [] },
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={agentToolPart}
          partIdx={0}
          id="msg-agent-tool"
          role="assistant"
          isLastMessage
          status="ready"
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Agent: case-management")).toBeInTheDocument()
  })

  it("renders the stop divider for a cancelled turn marker", () => {
    const cancelledPart = {
      type: "data-cancelled",
      data: { reason: "user_cancel" },
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={cancelledPart}
          partIdx={0}
          id="msg-cancelled"
          role="assistant"
          isLastMessage
          status="ready"
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Interrupted")).toBeInTheDocument()
    expect(screen.queryByText("Chat stopped")).not.toBeInTheDocument()
  })

  it("marks pending tool calls as interrupted in a cancelled turn", () => {
    const pendingToolPart = {
      type: "tool-core__table__list_tables",
      toolCallId: "tc-pending-1",
      state: "input-available",
      input: {},
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={pendingToolPart}
          partIdx={0}
          id="msg-pending-tool"
          role="assistant"
          isLastMessage
          status="ready"
          turnCancelled
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Interrupted")).toBeInTheDocument()
    expect(screen.queryByText("In progress")).not.toBeInTheDocument()
  })

  it("renders SDK abort errors as interrupted instead of leaking them", () => {
    const abortedToolPart = {
      type: "tool-core__table__list_tables",
      toolCallId: "tc-aborted-1",
      state: "output-error",
      input: {},
      errorText: "MCP error -32001: AbortError: The operation was aborted.",
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={abortedToolPart}
          partIdx={0}
          id="msg-aborted-tool"
          role="assistant"
          isLastMessage
          status="ready"
          turnCancelled
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Interrupted")).toBeInTheDocument()
    expect(screen.queryByText("Error")).not.toBeInTheDocument()
  })

  it("marks tool calls listed in the structured interrupt metadata as interrupted", () => {
    // Backend-recorded abort casualty: the error text is generic (no abort
    // phrasing), so only the structured tool_call_ids signal can flag it.
    const abortedToolPart = {
      type: "tool-core__table__list_tables",
      toolCallId: "tc-structured-1",
      state: "output-error",
      input: {},
      errorText: "connection reset",
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={abortedToolPart}
          partIdx={0}
          id="msg-structured-tool"
          role="assistant"
          isLastMessage
          status="ready"
          turnCancelled
          interruptedToolCallIds={new Set(["tc-structured-1"])}
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Interrupted")).toBeInTheDocument()
    expect(screen.queryByText("Error")).not.toBeInTheDocument()
  })

  it("marks structurally interrupted tool calls even without the turn flag", () => {
    // Live streams can land the cancelled marker on a different message than
    // the tool calls it aborted, so the message holding the tools never gets
    // turnCancelled. The backend-recorded ids must flag them on their own.
    const abortedToolPart = {
      type: "tool-core__table__list_tables",
      toolCallId: "tc-structured-2",
      state: "output-error",
      input: {},
      errorText: "MCP error -32001: AbortError: The operation was aborted.",
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={abortedToolPart}
          partIdx={0}
          id="msg-structured-tool-no-flag"
          role="assistant"
          isLastMessage
          status="ready"
          interruptedToolCallIds={new Set(["tc-structured-2"])}
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Interrupted")).toBeInTheDocument()
    expect(screen.queryByText("Error")).not.toBeInTheDocument()
  })

  it("keeps genuine tool errors as errors in a cancelled turn", () => {
    const failedToolPart = {
      type: "tool-core__table__list_tables",
      toolCallId: "tc-failed-1",
      state: "output-error",
      input: {},
      errorText: "Table not found: alerts",
    } as unknown as Parameters<typeof MessagePart>[0]["part"]

    render(
      <TooltipProvider>
        <MessagePart
          part={failedToolPart}
          partIdx={0}
          id="msg-failed-tool"
          role="assistant"
          isLastMessage
          status="ready"
          turnCancelled
        />
      </TooltipProvider>
    )

    expect(screen.getByText("Error")).toBeInTheDocument()
    expect(screen.queryByText("Interrupted")).not.toBeInTheDocument()
  })

  function mockUseVercelChatStatus(status: "ready" | "submitted") {
    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status,
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)
  }

  it("shows the empty hero for artifact-only workspace chat messages", () => {
    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [
        {
          id: "message-1",
          role: "assistant",
          parts: [
            {
              type: "data-artifact",
              data: {
                op: "upsert",
                artifact: {
                  id: "artifact-1",
                  title: "Investigation notes",
                  type: "generic",
                },
              },
            },
          ],
        },
      ],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="copilot"
            entityId="workspace-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            surface="workspace-chat"
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(
      screen.getByText("What should we get done, Daryl?")
    ).toBeInTheDocument()
  })

  it("logs and recovers when sendMessage throws", async () => {
    const sendMessage = jest.fn(() => {
      throw new Error("network down")
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage,
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "Hello" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: "Hello" })
    })

    expect(console.error).toHaveBeenCalledWith(
      "Failed to send message:",
      expect.objectContaining({ message: "network down" })
    )

    await waitFor(() => {
      expect(textarea).toHaveValue("")
    })
  })

  it("renders dots indicator while status is submitted", () => {
    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "submitted",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(screen.getByTestId("dots-loader")).toBeInTheDocument()
  })

  it("does not refocus after response if the textarea was not focused before disabling", async () => {
    mockUseVercelChatStatus("ready")

    const renderSubject = () => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    const { rerender } = render(renderSubject())
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement
    const focusSpy = jest.spyOn(textarea, "focus")

    mockUseVercelChatStatus("submitted")
    rerender(renderSubject())
    expect(textarea).toBeDisabled()

    mockUseVercelChatStatus("ready")
    rerender(renderSubject())

    await waitFor(() => {
      expect(textarea).toBeEnabled()
      expect(focusSpy).not.toHaveBeenCalled()
    })
    expect(textarea).not.toHaveFocus()
  })

  it("does not refocus after response if focus moves outside while waiting", async () => {
    mockUseVercelChatStatus("ready")

    const renderSubject = () => (
      <>
        <button type="button">Outside target</button>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <ChatSessionPane
              chat={createChatFixture()}
              workspaceId="workspace-1"
              entityType="case"
              entityId="case-1"
              modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            />
          </TooltipProvider>
        </QueryClientProvider>
      </>
    )

    const { rerender } = render(renderSubject())
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement
    textarea.focus()
    fireEvent.focus(textarea)
    expect(textarea).toHaveFocus()
    const focusSpy = jest.spyOn(textarea, "focus")

    mockUseVercelChatStatus("submitted")
    rerender(renderSubject())
    expect(textarea).toBeDisabled()

    const outsideTarget = screen.getByRole("button", { name: "Outside target" })
    fireEvent.pointerDown(outsideTarget)
    outsideTarget.focus()
    expect(outsideTarget).toHaveFocus()

    mockUseVercelChatStatus("ready")
    rerender(renderSubject())

    await waitFor(() => {
      expect(textarea).toBeEnabled()
      expect(focusSpy).not.toHaveBeenCalled()
    })
    expect(outsideTarget).toHaveFocus()
  })

  it("refocuses after response if the textarea had focus before disabling", async () => {
    mockUseVercelChatStatus("ready")

    const renderSubject = () => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    const { rerender } = render(renderSubject())
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement
    textarea.focus()
    fireEvent.focus(textarea)
    expect(textarea).toHaveFocus()
    const focusSpy = jest.spyOn(textarea, "focus")

    mockUseVercelChatStatus("submitted")
    rerender(renderSubject())
    expect(textarea).toBeDisabled()

    mockUseVercelChatStatus("ready")
    rerender(renderSubject())

    await waitFor(() => {
      expect(textarea).toBeEnabled()
      expect(focusSpy).toHaveBeenCalledTimes(1)
    })
    expect(textarea).toHaveFocus()
  })

  it("preserves refocus when submit button blur omits related target", async () => {
    mockUseVercelChatStatus("ready")

    const renderSubject = () => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    const { rerender } = render(renderSubject())
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement
    textarea.focus()
    fireEvent.focus(textarea)
    fireEvent.change(textarea, { target: { value: "Hello" } })
    const focusSpy = jest.spyOn(textarea, "focus")

    const submitButton = screen.getByRole("button", { name: "Submit" })
    expect(submitButton).toBeEnabled()
    fireEvent.pointerDown(submitButton)
    fireEvent.blur(textarea, { relatedTarget: null })

    mockUseVercelChatStatus("submitted")
    rerender(renderSubject())
    expect(textarea).toBeDisabled()

    mockUseVercelChatStatus("ready")
    rerender(renderSubject())

    await waitFor(() => {
      expect(textarea).toBeEnabled()
      expect(focusSpy).toHaveBeenCalledTimes(1)
    })
    expect(textarea).toHaveFocus()
  })

  it("submits approval decisions with continue payload", async () => {
    const sendMessage = jest.fn().mockResolvedValue(undefined)
    const clearError = jest.fn()

    mockUseVercelChat.mockReturnValue({
      sendMessage,
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [
        {
          id: "msg-approval",
          role: "assistant",
          parts: [
            {
              type: "data-approval-request",
              data: [
                {
                  tool_call_id: "tc-1",
                  tool_name: "core__cases__list_cases",
                  args: { limit: 100 },
                },
              ],
            },
          ],
        },
      ],
      status: "ready",
      lastError: null,
      clearError,
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(screen.getByText("core.cases.list_cases")).toBeInTheDocument()
    expect(screen.getByText("Approval required")).toBeInTheDocument()
    const approvalSubmit = screen
      .getAllByRole("button", { name: "Submit" })
      .find((button) => button.textContent?.trim() === "Submit")
    if (!approvalSubmit) {
      throw new Error("Approval submit button not found")
    }
    expect(approvalSubmit).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Edit + approve" }))
    const overrideEditor = screen.getByTestId("mock-code-editor")
    expect((overrideEditor as HTMLTextAreaElement).value).toContain(
      '"limit": 100'
    )

    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    expect(approvalSubmit).toBeEnabled()

    fireEvent.click(approvalSubmit)

    await waitFor(() => {
      expect(clearError).toHaveBeenCalled()
    })
    expect(sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: "user",
        parts: [
          expect.objectContaining({
            type: "data-continue",
            data: expect.objectContaining({
              format: "continue",
              decisions: [
                expect.objectContaining({
                  tool_call_id: "tc-1",
                  action: "approve",
                }),
              ],
            }),
          }),
        ],
      })
    )
  })

  it("submits selected approval decisions without requiring the full batch", async () => {
    const sendMessage = jest.fn().mockResolvedValue(undefined)

    mockUseVercelChat.mockReturnValue({
      sendMessage,
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [
        {
          id: "msg-approval",
          role: "assistant",
          parts: [
            {
              type: "data-approval-request",
              data: [
                {
                  tool_call_id: "tc-1",
                  tool_name: "core__http_request",
                  args: { url: "https://example.com" },
                },
                {
                  tool_call_id: "tc-2",
                  tool_name: "core__http_request",
                  args: { url: "https://example.org" },
                },
              ],
            },
          ],
        },
      ],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    const firstCard = screen.getByTestId("approval-card-tc-1")
    const secondCard = screen.getByTestId("approval-card-tc-2")
    const approvalSubmit = screen
      .getAllByRole("button", { name: "Submit" })
      .find((button) => button.textContent?.trim() === "Submit")
    if (!approvalSubmit) {
      throw new Error("Approval submit button not found")
    }

    expect(approvalSubmit).toBeDisabled()

    fireEvent.click(within(firstCard).getByRole("button", { name: "Approve" }))
    expect(approvalSubmit).toBeEnabled()

    fireEvent.click(approvalSubmit)

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledTimes(1)
    })
    expect(sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        parts: [
          expect.objectContaining({
            data: expect.objectContaining({
              decisions: [
                expect.objectContaining({
                  tool_call_id: "tc-1",
                  action: "approve",
                }),
              ],
            }),
          }),
        ],
      })
    )

    expect(
      within(firstCard).queryByLabelText("Decision: Approved")
    ).not.toBeInTheDocument()
    expect(firstCard).toHaveClass("bg-muted/10")
    expect(firstCard).not.toHaveClass("opacity-75")
    const submittedApproveButton = within(firstCard).getByRole("button", {
      name: "Approve",
    })
    expect(submittedApproveButton).toBeDisabled()
    expect(submittedApproveButton).toHaveClass("border-success/55")
    expect(submittedApproveButton).toHaveClass("text-success")
    expect(submittedApproveButton).toHaveClass("disabled:opacity-100")
    expect(
      within(secondCard).queryByLabelText("Decision: Approved")
    ).not.toBeInTheDocument()
  })

  it("mounts previously submitted approval cards as disabled", () => {
    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [
        {
          id: "msg-approval",
          role: "assistant",
          parts: [
            {
              type: "data-approval-request",
              data: [
                {
                  tool_call_id: "tc-1",
                  tool_name: "core__http_request",
                  args: { url: "https://example.com" },
                  status: "approved",
                  decision: true,
                },
                {
                  tool_call_id: "tc-2",
                  tool_name: "core__http_request",
                  args: { url: "https://example.org" },
                  status: "pending",
                },
              ],
            },
          ],
        },
      ],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    const firstCard = screen.getByTestId("approval-card-tc-1")
    const secondCard = screen.getByTestId("approval-card-tc-2")
    const approvalSubmit = screen
      .getAllByRole("button", { name: "Submit" })
      .find((button) => button.textContent?.trim() === "Submit")
    if (!approvalSubmit) {
      throw new Error("Approval submit button not found")
    }

    expect(firstCard).toHaveClass("bg-muted/10")
    expect(
      within(firstCard).getByRole("button", { name: "Approve" })
    ).toBeDisabled()
    expect(
      within(firstCard).getByRole("button", { name: "Approve" })
    ).toHaveClass("border-success/55")
    expect(
      within(secondCard).getByRole("button", { name: "Approve" })
    ).toBeEnabled()
    expect(approvalSubmit).toBeDisabled()
  })

  it("passes tools picked in draft mode with the first send", async () => {
    const onBeforeSend = jest.fn().mockResolvedValue("chat-2")
    const action = {
      id: "action-1",
      name: "core.cases.list_cases",
      action: "core.cases.list_cases",
      default_title: "List cases",
      description: "List all cases",
      namespace: "core.cases",
      type: "template" as const,
      origin: "tracecat://test",
      availability: { locked: false, missing_entitlements: [] },
    }
    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions: [action],
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: (key: string) =>
        key === action.action ? action : undefined,
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
            onBeforeSend={onBeforeSend}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    await addToolFromPicker("List cases")

    expect(
      screen.getByRole("button", { name: /remove list cases/i })
    ).toBeInTheDocument()

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "hello" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(onBeforeSend).toHaveBeenCalledWith(
        "hello",
        ["core.cases.list_cases"],
        []
      )
    })
  })

  it("serializes tool persistence updates to avoid stale writes", async () => {
    const action = {
      id: "action-1",
      name: "core.cases.list_cases",
      action: "core.cases.list_cases",
      default_title: "List cases",
      description: "List all cases",
      namespace: "core.cases",
      type: "template" as const,
      origin: "tracecat://test",
      availability: { locked: false, missing_entitlements: [] },
    }
    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions: [action],
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: (key: string) =>
        key === action.action ? action : undefined,
    })

    const firstWrite = createDeferred<void>()
    const secondWrite = createDeferred<void>()
    const updateChat = jest
      .fn()
      .mockImplementationOnce(() => firstWrite.promise)
      .mockImplementationOnce(() => secondWrite.promise)
    mockUseUpdateChat.mockReturnValue({
      updateChat,
      isUpdating: false,
      updateError: null,
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    await addToolFromPicker("List cases")

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })
    expect(updateChat).toHaveBeenNthCalledWith(1, {
      chatId: "chat-1",
      update: { tools: ["core.cases.list_cases"] },
    })

    fireEvent.click(screen.getByRole("button", { name: /remove list cases/i }))
    expect(updateChat).toHaveBeenCalledTimes(1)

    firstWrite.resolve()
    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(2)
    })
    expect(updateChat).toHaveBeenNthCalledWith(2, {
      chatId: "chat-1",
      update: { tools: [] },
    })

    secondWrite.resolve()
  })

  it("waits for pending tool persistence before sending a message", async () => {
    const action = {
      id: "action-1",
      name: "core.cases.list_cases",
      action: "core.cases.list_cases",
      default_title: "List cases",
      description: "List all cases",
      namespace: "core.cases",
      type: "template" as const,
      origin: "tracecat://test",
      availability: { locked: false, missing_entitlements: [] },
    }
    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions: [action],
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: (key: string) =>
        key === action.action ? action : undefined,
    })

    const toolWrite = createDeferred<void>()
    const updateChat = jest.fn().mockImplementation(() => toolWrite.promise)
    mockUseUpdateChat.mockReturnValue({
      updateChat,
      isUpdating: false,
      updateError: null,
    })

    const sendMessage = jest.fn().mockResolvedValue(undefined)
    mockUseVercelChat.mockReturnValue({
      sendMessage,
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    await addToolFromPicker("List cases")

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "hello" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    expect(sendMessage).not.toHaveBeenCalled()

    toolWrite.resolve()
    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: "hello" })
    })
  })

  it("waits for pending MCP persistence before sending a message", async () => {
    const integration = createMcpIntegrationFixture()
    mockUseListMcpIntegrations.mockReturnValue({
      mcpIntegrations: [integration],
      mcpIntegrationsIsLoading: false,
      mcpIntegrationsError: null,
    })

    const mcpWrite = createDeferred<void>()
    const updateChat = jest.fn().mockImplementation(() => mcpWrite.promise)
    mockUseUpdateChat.mockReturnValue({
      updateChat,
      isUpdating: false,
      updateError: null,
    })

    const sendMessage = jest.fn().mockResolvedValue(undefined)
    mockUseVercelChat.mockReturnValue({
      sendMessage,
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({
              entity_type: "copilot",
              entity_id: "workspace-1",
            })}
            workspaceId="workspace-1"
            entityType="copilot"
            entityId="workspace-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            surface="workspace-chat"
            toolsEnabled
            mcpEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    fireEvent.click(screen.getByRole("button", { name: "Tools" }))
    fireEvent.click(await screen.findByText("RunReveal"))

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })
    expect(updateChat).toHaveBeenCalledWith({
      chatId: "chat-1",
      update: { mcp_integrations: ["mcp-1"] },
    })

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "hello" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    expect(sendMessage).not.toHaveBeenCalled()

    mcpWrite.resolve()
    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: "hello" })
    })
  })

  it("keeps optimistic MCP selections while queued persistence catches up", async () => {
    const firstIntegration = createMcpIntegrationFixture({
      id: "mcp-1",
      name: "RunReveal",
    })
    const secondIntegration = createMcpIntegrationFixture({
      id: "mcp-2",
      name: "Okta",
    })
    mockUseListMcpIntegrations.mockReturnValue({
      mcpIntegrations: [firstIntegration, secondIntegration],
      mcpIntegrationsIsLoading: false,
      mcpIntegrationsError: null,
    })

    const firstWrite = createDeferred<void>()
    const secondWrite = createDeferred<void>()
    const updateChat = jest
      .fn()
      .mockImplementationOnce(() => firstWrite.promise)
      .mockImplementationOnce(() => secondWrite.promise)
    mockUseUpdateChat.mockReturnValue({
      updateChat,
      isUpdating: false,
      updateError: null,
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({
              entity_type: "copilot",
              entity_id: "workspace-1",
            })}
            workspaceId="workspace-1"
            entityType="copilot"
            entityId="workspace-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            surface="workspace-chat"
            toolsEnabled
            mcpEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    fireEvent.click(screen.getByRole("button", { name: "Tools" }))
    fireEvent.click(await screen.findByText("RunReveal"))
    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByRole("button", { name: "Tools (1)" }))
    fireEvent.click(await screen.findByText("Okta"))
    expect(
      screen.getByRole("button", { name: "Tools (2)" })
    ).toBeInTheDocument()
    expect(updateChat).toHaveBeenCalledTimes(1)

    firstWrite.resolve()
    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(2)
    })

    rerender(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({
              entity_type: "copilot",
              entity_id: "workspace-1",
              mcp_integrations: ["mcp-1"],
            })}
            workspaceId="workspace-1"
            entityType="copilot"
            entityId="workspace-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            surface="workspace-chat"
            toolsEnabled
            mcpEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(
      screen.getByRole("button", { name: "Tools (2)" })
    ).toBeInTheDocument()

    secondWrite.resolve()
  })

  it("does not fetch MCP integrations for non-MCP chat surfaces", () => {
    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(mockUseListMcpIntegrations).toHaveBeenCalledWith(
      "workspace-1",
      undefined,
      { enabled: false }
    )
  })

  it("does not fetch MCP integrations for copilot when MCP is disabled", () => {
    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({
              entity_type: "copilot",
              entity_id: "workspace-1",
            })}
            workspaceId="workspace-1"
            entityType="copilot"
            entityId="workspace-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            surface="workspace-chat"
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(mockUseListMcpIntegrations).toHaveBeenCalledWith(
      "workspace-1",
      undefined,
      { enabled: false }
    )
  })

  it("does not persist tool selection twice in StrictMode", async () => {
    const action = {
      id: "action-1",
      name: "core.cases.list_cases",
      action: "core.cases.list_cases",
      default_title: "List cases",
      description: "List all cases",
      namespace: "core.cases",
      type: "template" as const,
      origin: "tracecat://test",
      availability: { locked: false, missing_entitlements: [] },
    }
    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions: [action],
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: (key: string) =>
        key === action.action ? action : undefined,
    })

    const updateChat = jest.fn().mockResolvedValue(undefined)
    mockUseUpdateChat.mockReturnValue({
      updateChat,
      isUpdating: false,
      updateError: null,
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <ChatSessionPane
              chat={createChatFixture()}
              workspaceId="workspace-1"
              entityType="case"
              entityId="case-1"
              modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
              toolsEnabled
            />
          </TooltipProvider>
        </QueryClientProvider>
      </StrictMode>
    )

    await addToolFromPicker("List cases")

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })
  })

  it("keeps optimistic tool chips while queued persistence catches up", async () => {
    const listCases = {
      id: "action-1",
      name: "core.cases.list_cases",
      action: "core.cases.list_cases",
      default_title: "List cases",
      description: "List all cases",
      namespace: "core.cases",
      type: "template" as const,
      origin: "tracecat://test",
      availability: { locked: false, missing_entitlements: [] },
    }
    const getCase = {
      id: "action-2",
      name: "core.cases.get_case",
      action: "core.cases.get_case",
      default_title: "Get case",
      description: "Get a case",
      namespace: "core.cases",
      type: "template" as const,
      origin: "tracecat://test",
      availability: { locked: false, missing_entitlements: [] },
    }
    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions: [listCases, getCase],
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: (key: string) =>
        key === listCases.action
          ? listCases
          : key === getCase.action
            ? getCase
            : undefined,
    })

    const firstWrite = createDeferred<void>()
    const secondWrite = createDeferred<void>()
    const updateChat = jest
      .fn()
      .mockImplementationOnce(() => firstWrite.promise)
      .mockImplementationOnce(() => secondWrite.promise)
    mockUseUpdateChat.mockReturnValue({
      updateChat,
      isUpdating: false,
      updateError: null,
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage: jest.fn(),
      setMessages: jest.fn(),
      regenerate: jest.fn(),
      messages: [],
      status: "ready",
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture()}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    await addToolFromPicker("List cases")

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })

    await addToolFromPicker("Get case")

    expect(
      screen.getByRole("button", { name: /remove list cases/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /remove get case/i })
    ).toBeInTheDocument()
    expect(updateChat).toHaveBeenCalledTimes(1)

    firstWrite.resolve()
    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(2)
    })

    rerender(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({ tools: ["core.cases.list_cases"] })}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(
      screen.getByRole("button", { name: /remove list cases/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /remove get case/i })
    ).toBeInTheDocument()

    secondWrite.resolve()
    rerender(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({
              tools: ["core.cases.list_cases", "core.cases.get_case"],
            })}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            toolsEnabled
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    expect(
      screen.getByRole("button", { name: /remove list cases/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /remove get case/i })
    ).toBeInTheDocument()
  })

  describe("agent mentions", () => {
    function renderPane(
      props: Partial<Parameters<typeof ChatSessionPane>[0]> = {}
    ) {
      return render(
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <ChatSessionPane
              chat={createChatFixture()}
              workspaceId="workspace-1"
              entityType="case"
              entityId="case-1"
              modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
              toolsEnabled
              {...props}
            />
          </TooltipProvider>
        </QueryClientProvider>
      )
    }

    let sendMessage: jest.Mock

    beforeEach(() => {
      sendMessage = jest.fn()
      mockUseVercelChat.mockReturnValue({
        sendMessage,
        setMessages: jest.fn(),
        regenerate: jest.fn(),
        messages: [],
        status: "ready",
        lastError: null,
        clearError: jest.fn(),
        // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
      } as any)
      mockPresets([TRIAGE_PRESET])
    })

    it("ignores a second Enter while the preset write is still in flight", async () => {
      // The optimistic selectedPresetId flips before updateChat resolves, so a
      // second Enter used to skip the write it was still waiting on and send
      // under the server's previous preset -- then the first submit resumed and
      // sent again.
      let resolvePreset: (applied: boolean) => void = () => {}
      const onSelect = jest.fn().mockReturnValue(
        new Promise<boolean>((resolve) => {
          resolvePreset = resolve
        })
      )
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@tri", selectionStart: 4 },
      })
      await screen.findByText("Triage agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(textarea).toHaveValue("@Triage agent "))

      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1))

      // Second Enter lands while the write is pending.
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      expect(sendMessage).not.toHaveBeenCalled()

      resolvePreset(true)

      await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1))
      expect(onSelect).toHaveBeenCalledTimes(1)
    })

    it("keeps a draft typed while the preset write is in flight", async () => {
      // The composer stays editable across the write, so the send that follows
      // must not clear text the user has since started on.
      let resolvePreset: (applied: boolean) => void = () => {}
      const onSelect = jest.fn().mockReturnValue(
        new Promise<boolean>((resolve) => {
          resolvePreset = resolve
        })
      )
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@tri", selectionStart: 4 },
      })
      await screen.findByText("Triage agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(textarea).toHaveValue("@Triage agent "))

      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1))

      // Typed after Enter, while the write is still pending.
      fireEvent.change(textarea, {
        target: { value: "@Triage agent next question", selectionStart: 27 },
      })

      resolvePreset(true)

      await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1))
      // The turn still sends what Enter submitted, not the newer draft.
      expect(sendMessage).toHaveBeenCalledWith(
        expect.objectContaining({ text: "@Triage agent " })
      )
      // Only the tail survives. Leaving the sent prefix behind would send it
      // a second time on the next submit.
      expect(textarea).toHaveValue("next question")
    })

    it("replaces the first agent when a second one is picked", async () => {
      // A chat session owns one preset, so a stale name left in the text would
      // quietly win at submit.
      mockPresets([TRIAGE_PRESET, MALWARE_PRESET])
      const onSelect = jest.fn().mockResolvedValue(true)
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@tri", selectionStart: 4 },
      })
      await screen.findByText("Triage agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(textarea).toHaveValue("@Triage agent "))

      fireEvent.change(textarea, {
        target: { value: "@Triage agent @mal", selectionStart: 18 },
      })
      await screen.findByText("Malware agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

      await waitFor(() => expect(textarea).toHaveValue(" @Malware agent "))

      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(onSelect).toHaveBeenCalledWith("preset-2"))
      expect(onSelect).not.toHaveBeenCalledWith("preset-1")
    })

    it("keeps the list open while a multi-word name is typed out", async () => {
      // Preset names contain spaces, so a query that stopped at the first one
      // made a hand-typed mention impossible to select and silently unbound.
      const onSelect = jest.fn().mockResolvedValue(true)
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@Triage ag", selectionStart: 10 },
      })

      await screen.findByText("Triage agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(textarea).toHaveValue("@Triage agent "))

      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(onSelect).toHaveBeenCalledWith("preset-1"))
    })

    it("releases the trigger once a multi-word query matches nothing", async () => {
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect: jest.fn().mockResolvedValue(true),
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@Triage ag", selectionStart: 10 },
      })
      await screen.findByText("Triage agent")

      // Prose after a stray trigger must not trail an open popover.
      fireEvent.change(textarea, {
        target: { value: "@Triage ag please look at this", selectionStart: 30 },
      })

      await waitFor(() =>
        expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
      )
      expect(screen.queryByText("No agents found")).not.toBeInTheDocument()
    })

    it("sets the session preset from an @ mention and sends the text verbatim", async () => {
      const onSelect = jest.fn().mockResolvedValue(true)
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@tri", selectionStart: 4 },
      })

      await screen.findByText("Triage agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

      await waitFor(() => expect(textarea).toHaveValue("@Triage agent "))
      expect(sendMessage).not.toHaveBeenCalled()

      fireEvent.change(textarea, {
        target: { value: "@Triage agent hello", selectionStart: 19 },
      })
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

      await waitFor(() => expect(onSelect).toHaveBeenCalledWith("preset-1"))
      await waitFor(() =>
        expect(sendMessage).toHaveBeenCalledWith({
          text: "@Triage agent hello",
        })
      )
    })

    it("says the lookup failed rather than claiming there are no agents", async () => {
      mockPresetsError()
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect: jest.fn().mockResolvedValue(true),
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, { target: { value: "@", selectionStart: 1 } })

      expect(
        await screen.findByText("Could not load agents. Try again.")
      ).toBeInTheDocument()
      expect(screen.queryByText("No agents found")).not.toBeInTheDocument()
    })

    it("does not send when the mentioned preset fails to persist", async () => {
      // A failed preset write must not fall through and run the turn under the
      // previous agent; the draft stays put so the user can retry.
      const onSelect = jest.fn().mockResolvedValue(false)
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@tri", selectionStart: 4 },
      })

      await screen.findByText("Triage agent")
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() => expect(textarea).toHaveValue("@Triage agent "))

      fireEvent.change(textarea, {
        target: { value: "@Triage agent hello", selectionStart: 19 },
      })
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

      await waitFor(() => expect(onSelect).toHaveBeenCalledWith("preset-1"))
      expect(sendMessage).not.toHaveBeenCalled()
      expect(textarea).toHaveValue("@Triage agent hello")
      // The submit rejects, so `submitPrompt` skips the cleanup that would
      // clear attachments for a message it never sent.
      await act(async () => {
        await Promise.resolve()
      })
      expect(textarea).toHaveValue("@Triage agent hello")

      // Rejecting must still release the in-flight guard, or the composer
      // would be wedged and no later submit could get through.
      onSelect.mockResolvedValue(true)
      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
      await waitFor(() =>
        expect(sendMessage).toHaveBeenCalledWith({
          text: "@Triage agent hello",
        })
      )
    })

    it("no longer offers tools on @", async () => {
      const action = {
        id: "action-1",
        name: "core.cases.list_cases",
        action: "core.cases.list_cases",
        default_title: "List cases",
        description: "List all cases",
        namespace: "core.cases",
        type: "template" as const,
        origin: "tracecat://test",
        availability: { locked: false, missing_entitlements: [] },
      }
      mockUseBuilderRegistryActions.mockReturnValue({
        registryActions: [action],
        registryActionsIsLoading: false,
        registryActionsError: null,
        getRegistryAction: (key: string) =>
          key === action.action ? action : undefined,
      })

      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect: jest.fn().mockResolvedValue(true),
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, {
        target: { value: "@li", selectionStart: 3 },
      })

      // `@` is the agent picker now: it says there is no matching agent rather
      // than offering the registry tool that used to match this query.
      expect(screen.queryByText("List cases")).not.toBeInTheDocument()
      expect(await screen.findByText("No agents found")).toBeInTheDocument()
    })

    it("opens the Enterprise lock row without agent add-ons, and Enter still submits", async () => {
      mockEntitled(false)
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect: jest.fn().mockResolvedValue(true),
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.change(textarea, { target: { value: "@", selectionStart: 1 } })

      expect(
        await screen.findByText("Agent mentions are an Enterprise feature")
      ).toBeInTheDocument()
      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
      expect(mockUseAgentPresets).toHaveBeenCalledWith(
        "workspace-1",
        expect.objectContaining({ enabled: false })
      )

      fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

      await waitFor(() =>
        expect(sendMessage).toHaveBeenCalledWith({ text: "@" })
      )
    })

    it("shows the mention hint while the composer is focused and empty", () => {
      renderPane({
        agentMentionsSupported: true,
        presetSelector: {
          label: "No preset",
          selectedPresetId: null,
          onSelect: jest.fn().mockResolvedValue(true),
        },
      })

      const textarea = screen.getByRole("textbox")
      expect(screen.queryByTestId("composer-hint")).not.toBeInTheDocument()

      fireEvent.focus(textarea)

      const hint = screen.getByTestId("composer-hint")
      expect(hint).toHaveTextContent("Mention agent")
      expect(hint).not.toHaveTextContent("Run workflow")

      fireEvent.change(textarea, {
        target: { value: "hello", selectionStart: 5 },
      })
      expect(screen.queryByTestId("composer-hint")).not.toBeInTheDocument()

      fireEvent.change(textarea, { target: { value: "", selectionStart: 0 } })
      expect(screen.getByTestId("composer-hint")).toBeInTheDocument()

      fireEvent.blur(textarea)
      expect(screen.queryByTestId("composer-hint")).not.toBeInTheDocument()
    })

    it("leaves @ inert on surfaces that do not support presets", () => {
      renderPane()

      const textarea = screen.getByRole("textbox")
      fireEvent.focus(textarea)
      expect(screen.queryByTestId("composer-hint")).not.toBeInTheDocument()

      fireEvent.change(textarea, { target: { value: "@", selectionStart: 1 } })

      expect(screen.queryByText("Triage agent")).not.toBeInTheDocument()
      expect(
        screen.queryByText("Agent mentions are an Enterprise feature")
      ).not.toBeInTheDocument()
      expect(textarea).toHaveValue("@")
    })

    it("replaces the hint with the active preset badge and clears back to tools", () => {
      const onSelect = jest.fn().mockResolvedValue(true)
      renderPane({
        agentMentionsSupported: true,
        toolsEnabled: false,
        presetSelector: {
          label: "Triage agent",
          selectedPresetId: "preset-1",
          onSelect,
        },
      })

      const textarea = screen.getByRole("textbox")
      fireEvent.focus(textarea)

      expect(screen.getByText("Triage agent")).toBeInTheDocument()
      expect(screen.queryByTestId("composer-hint")).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: /^Tools/ })
      ).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole("button", { name: "Clear agent" }))

      expect(onSelect).toHaveBeenCalledWith(null)
    })
  })

  // Ownership swap at quiescent boundaries: while a turn streams the stream
  // owns the transcript; the moment status returns to `ready` the pane adopts
  // the server copy WHOLESALE (replace, never merge — DB and stream message ids
  // differ by design). The backend guarantees DB history and any continuation
  // stream never overlap, so adopting a shorter, longer, or same-length copy is
  // uniformly correct.
  describe("adopt-on-ready ownership swap", () => {
    const userTurn = (id: string, text: string) => ({
      id,
      role: "user" as const,
      parts: [{ type: "text" as const, text }],
    })

    function mockLiveMessages(
      messages: UIMessage[],
      setMessages: jest.Mock,
      status: "ready" | "streaming" | "submitted" = "ready"
    ): void {
      mockUseVercelChat.mockReturnValue({
        sendMessage: jest.fn(),
        setMessages,
        regenerate: jest.fn(),
        messages,
        status,
        lastError: null,
        clearError: jest.fn(),
        // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
      } as any)
    }

    const renderSubject = (serverMessages: UIMessage[]) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <ChatSessionPane
            chat={createChatFixture({ messages: serverMessages })}
            workspaceId="workspace-1"
            entityType="case"
            entityId="case-1"
            modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )

    // When the live list already matches the server copy there is nothing to
    // adopt, so the pane leaves useChat untouched.
    it("does not adopt when the server copy matches the live list", () => {
      const setMessages = jest.fn()
      const same = [userTurn("m1", "hello")]
      mockLiveMessages(same, setMessages)

      render(renderSubject(same))

      expect(setMessages).not.toHaveBeenCalled()
    })

    // A longer server copy (e.g. an approval resolved from another surface and
    // the refetch landed) is adopted wholesale.
    it("adopts a longer server copy", () => {
      const setMessages = jest.fn()
      mockLiveMessages([userTurn("m1", "hello")], setMessages)

      const { rerender } = render(renderSubject([userTurn("m1", "hello")]))
      expect(setMessages).not.toHaveBeenCalled()

      const advanced = [userTurn("m1", "hello"), userTurn("m2", "resolved")]
      rerender(renderSubject(advanced))

      expect(setMessages).toHaveBeenCalledTimes(1)
      expect(setMessages.mock.calls[0][0]).toHaveLength(2)
    })

    // onFinish can refetch before the backend clears curr_run_id, so a shorter
    // server copy may temporarily omit the just-finished turn. Keep the live
    // reply until a later server snapshot catches up.
    it("does not let a shorter server copy erase the live reply", () => {
      const setMessages = jest.fn()
      const live = [
        userTurn("m1", "turn one"),
        userTurn("m2", "reply one"),
        userTurn("m3", "turn two"),
      ]
      mockLiveMessages(live, setMessages)

      const server = [userTurn("m1", "turn one")]
      render(renderSubject(server))

      expect(setMessages).not.toHaveBeenCalled()
    })

    // Same length but different content (ids/text) is a distinct transcript and
    // must be adopted too.
    it("adopts a same-length server copy with different content", () => {
      const setMessages = jest.fn()
      mockLiveMessages([userTurn("m1", "hello")], setMessages)

      render(renderSubject([userTurn("s1", "server hello")]))

      expect(setMessages).toHaveBeenCalledTimes(1)
      expect(setMessages.mock.calls[0][0]).toHaveLength(1)
      expect(setMessages.mock.calls[0][0][0].id).toBe("s1")
    })

    // The stream owns the current turn: while status is streaming the pane must
    // never adopt, even when the server copy differs — that would clobber the
    // live turn.
    it("does not adopt while the stream is active", () => {
      const setMessages = jest.fn()
      const streamed = [userTurn("m1", "hello"), userTurn("m2", "world")]
      mockLiveMessages(streamed, setMessages, "streaming")

      const { rerender } = render(renderSubject([userTurn("m1", "hello")]))
      rerender(renderSubject([userTurn("m1", "hello")]))

      expect(setMessages).not.toHaveBeenCalled()
    })

    const approvalCardTurn = (id: string): UIMessage => ({
      id,
      role: "assistant" as const,
      parts: [
        {
          type: "data-approval-request",
          data: [
            {
              tool_call_id: `tc-${id}`,
              tool_name: "core__cases__list_cases",
              args: {},
            },
          ],
        } as unknown as UIMessage["parts"][number],
      ],
    })

    // Cross-surface resolution drops the approval card from the server copy, so
    // the deficit is fully explained by a resolved approval-card-only message.
    // The server copy is adopted and the stale card disappears.
    it("adopts when a shorter server copy only dropped a resolved approval card", () => {
      const setMessages = jest.fn()
      const live = [userTurn("m1", "hello"), approvalCardTurn("a1")]
      mockLiveMessages(live, setMessages)

      const server = [userTurn("m1", "hello")]
      render(renderSubject(server))

      expect(setMessages).toHaveBeenCalledTimes(1)
      expect(setMessages.mock.calls[0][0]).toHaveLength(1)
      expect(setMessages.mock.calls[0][0][0].id).toBe("m1")
    })

    // Regression: a shorter server copy missing a real (non-approval) turn is
    // the finalize race, not a resolved card. It must NOT be adopted.
    it("does not adopt when a shorter server copy is missing a real turn", () => {
      const setMessages = jest.fn()
      const live = [
        userTurn("m1", "hello"),
        approvalCardTurn("a1"),
        userTurn("m2", "real reply"),
      ]
      mockLiveMessages(live, setMessages)

      const server = [userTurn("m1", "hello")]
      render(renderSubject(server))

      expect(setMessages).not.toHaveBeenCalled()
    })
  })

  // Seam artifact: during an approval pause the DB prefix carries the paused
  // tool call; after approval the rotated suffix-only stream re-emits the same
  // call as a synthesized part in a new bubble (bare tool result from the
  // backend reconcile). The pane must render each toolCallId once — keep the
  // last copy (the stream one carries the live output) at the display layer.
  describe("tool call dedupe across the DB/stream seam", () => {
    const TOOL_LABEL = "core.cases.list_cases"

    function toolPart(
      toolCallId: string,
      state: "input-available" | "output-available"
    ): UIMessage["parts"][number] {
      return {
        type: "tool-core__cases__list_cases",
        toolCallId,
        state,
        input: { limit: 10 },
        ...(state === "output-available" ? { output: { cases: [] } } : {}),
      } as unknown as UIMessage["parts"][number]
    }

    function approvalPart(toolCallId: string): UIMessage["parts"][number] {
      return {
        type: "data-approval-request",
        data: [
          {
            tool_call_id: toolCallId,
            tool_name: "core__cases__list_cases",
            args: { limit: 10 },
          },
        ],
      } as unknown as UIMessage["parts"][number]
    }

    const assistantMessage = (
      id: string,
      parts: UIMessage["parts"]
    ): UIMessage => ({ id, role: "assistant", parts })

    function mockLiveMessages(messages: UIMessage[]): void {
      mockUseVercelChat.mockReturnValue({
        sendMessage: jest.fn(),
        setMessages: jest.fn(),
        regenerate: jest.fn(),
        messages,
        status: "ready",
        lastError: null,
        clearError: jest.fn(),
        // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
      } as any)
    }

    const renderSubject = () =>
      render(
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <ChatSessionPane
              chat={createChatFixture()}
              workspaceId="workspace-1"
              entityType="case"
              entityId="case-1"
              modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
            />
          </TooltipProvider>
        </QueryClientProvider>
      )

    it("renders a tool call duplicated across DB prefix and stream suffix once, last copy wins", () => {
      mockLiveMessages([
        // DB prefix bubble: paused call awaiting approval.
        assistantMessage("db-1", [
          toolPart("tc-1", "input-available"),
          approvalPart("tc-1"),
        ]),
        // Rotated suffix stream bubble: synthesized copy of the same call
        // carrying the reconciled output.
        assistantMessage("session:run", [
          toolPart("tc-1", "input-available"),
          toolPart("tc-1", "output-available"),
        ]),
      ])

      renderSubject()

      // One rendering total: the tool bubble once, and no lingering approval
      // card for the resolved call (the card renders the same action label).
      expect(screen.getAllByText(TOOL_LABEL)).toHaveLength(1)
    })

    it("renders duplicated approval request cards once before tool output arrives", () => {
      mockLiveMessages([
        assistantMessage("db-1", [approvalPart("tc-1")]),
        assistantMessage("session:run", [approvalPart("tc-1")]),
      ])

      renderSubject()

      expect(screen.getAllByText(TOOL_LABEL)).toHaveLength(1)
      expect(screen.getAllByText("Approve")).toHaveLength(1)
    })

    it("renders duplicated identical output copies once", () => {
      mockLiveMessages([
        assistantMessage("db-1", [toolPart("tc-1", "output-available")]),
        assistantMessage("session:run", [toolPart("tc-1", "output-available")]),
      ])

      renderSubject()

      expect(screen.getAllByText(TOOL_LABEL)).toHaveLength(1)
    })

    it("leaves distinct tool calls untouched", () => {
      mockLiveMessages([
        assistantMessage("m1", [toolPart("tc-1", "output-available")]),
        assistantMessage("m2", [toolPart("tc-2", "output-available")]),
      ])

      renderSubject()

      expect(screen.getAllByText(TOOL_LABEL)).toHaveLength(2)
    })
  })
})
