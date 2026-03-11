import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { StrictMode } from "react"
import type { AgentSessionReadVercel } from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useUpdateChat, useVercelChat } from "@/hooks/use-chat"
import { useBuilderRegistryActions } from "@/lib/hooks"

jest.mock("@/hooks/use-chat", () => ({
  useVercelChat: jest.fn(),
  useGetChat: jest.fn(() => ({ chat: null })),
  useUpdateChat: jest.fn(() => ({ updateChat: jest.fn(), isUpdating: false })),
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
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
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
  agent_preset_id: null,
  harness_type: null,
  created_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
  updated_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
  last_stream_id: null,
  messages: [],
  ...overrides,
  agent_preset_version_id: overrides?.agent_preset_version_id ?? null,
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

  it("logs and recovers when sendMessage throws", async () => {
    const sendMessage = jest.fn(() => {
      throw new Error("network down")
    })

    mockUseVercelChat.mockReturnValue({
      sendMessage,
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

  it("submits approval decisions with continue payload", async () => {
    const sendMessage = jest.fn().mockResolvedValue(undefined)
    const clearError = jest.fn()

    mockUseVercelChat.mockReturnValue({
      sendMessage,
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

  it("supports @ mention tools in draft mode and passes selected tools before first send", async () => {
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

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "@li" } })

    await screen.findByText("List cases")
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    expect(
      screen.getByRole("button", { name: /remove list cases/i })
    ).toBeInTheDocument()

    fireEvent.change(textarea, { target: { value: "hello" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(onBeforeSend).toHaveBeenCalledWith("hello", [
        "core.cases.list_cases",
      ])
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

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "@li" } })
    await screen.findByText("List cases")
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

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

    const textarea = screen.getByRole("textbox")

    fireEvent.change(textarea, { target: { value: "@li" } })
    await screen.findByText("List cases")
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })

    fireEvent.change(textarea, { target: { value: "hello" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    expect(sendMessage).not.toHaveBeenCalled()

    toolWrite.resolve()
    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({ text: "hello" })
    })
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

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "@li" } })
    await screen.findByText("List cases")
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

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

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "@li" } })
    await screen.findByText("List cases")
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(updateChat).toHaveBeenCalledTimes(1)
    })

    fireEvent.change(textarea, { target: { value: "@ge" } })
    await screen.findByText("Get case")
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

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
})
