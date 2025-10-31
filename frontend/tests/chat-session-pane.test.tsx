import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ChatReadVercel } from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { useVercelChat } from "@/hooks/use-chat"

jest.mock("@/hooks/use-chat", () => ({
  useVercelChat: jest.fn(),
  useGetChat: jest.fn(() => ({ chat: null })),
  useUpdateChat: jest.fn(() => ({ updateChat: jest.fn(), isUpdating: false })),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const mockUseVercelChat = useVercelChat as jest.MockedFunction<
  typeof useVercelChat
>

const createChatFixture = (
  overrides?: Partial<ChatReadVercel>
): ChatReadVercel => ({
  id: "chat-1",
  title: "Test Chat",
  user_id: "user-1",
  entity_type: "case",
  entity_id: "case-1",
  tools: [],
  created_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
  updated_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
  last_stream_id: null,
  messages: [],
  ...overrides,
})

describe("ChatSessionPane", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    jest.spyOn(console, "error").mockImplementation(() => undefined)
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
      status: undefined,
      lastError: null,
      clearError: jest.fn(),
      // biome-ignore lint/suspicious/noExplicitAny: mock return type needs flexibility for testing
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <ChatSessionPane
          chat={createChatFixture()}
          workspaceId="workspace-1"
          entityType="case"
          entityId="case-1"
          modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
        />
      </QueryClientProvider>
    )

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "Hello" } })

    const form = textarea.closest("form") as HTMLFormElement
    fireEvent.submit(form)

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
        <ChatSessionPane
          chat={createChatFixture()}
          workspaceId="workspace-1"
          entityType="case"
          entityId="case-1"
          modelInfo={{ name: "gpt-4o-mini", provider: "openai" }}
        />
      </QueryClientProvider>
    )

    expect(screen.getByTestId("dots-loader")).toBeInTheDocument()
  })
})
