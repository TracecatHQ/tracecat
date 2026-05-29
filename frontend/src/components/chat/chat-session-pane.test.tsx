import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"

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
  Tool: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ToolContent: ({ children }: { children?: ReactNode }) => (
    <div>{children}</div>
  ),
  ToolHeader: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ToolInput: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ToolOutput: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
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
  parseChatError: (error: unknown) =>
    error instanceof Error ? error.message : "Chat error",
  useUpdateChat: () => ({
    isUpdating: false,
    updateChat: jest.fn(),
  }),
  useVercelChat: () => ({
    clearError: jest.fn(),
    lastError: null,
    messages: [],
    regenerate: jest.fn(),
    sendMessage: jest.fn(),
    status: "ready",
  }),
}))

jest.mock("@/lib/hooks", () => ({
  useBuilderRegistryActions: () => ({
    registryActions: [],
    registryActionsIsLoading: false,
  }),
}))

function renderChatSessionPane(
  props: Partial<Parameters<typeof ChatSessionPane>[0]> = {}
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ChatSessionPane
        workspaceId="workspace-1"
        modelInfo={{ name: "gpt-test", provider: "openai" }}
        placeholder="Ask Tracecat..."
        inputDisabledPlaceholder="Creating chat..."
        surface="workspace-chat"
        toolsEnabled={false}
        {...props}
      />
    </QueryClientProvider>
  )
}

describe("ChatSessionPane optimistic first send", () => {
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
      expect(onBeforeSend).toHaveBeenCalledWith("Summarize this workspace", [])
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
})
