import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type {
  AgentSessionRead,
  AgentSessionReadVercel,
  AgentSessionReadWithMessages,
} from "@/client"
import { agentSessionsUpdateSession } from "@/client"
import { useUpdateChat } from "@/hooks/use-chat"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    agentSessionsUpdateSession: jest.fn(),
  }
})

const mockAgentSessionsUpdateSession =
  agentSessionsUpdateSession as jest.MockedFunction<
    typeof agentSessionsUpdateSession
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

function createSessionRead(
  overrides?: Partial<AgentSessionRead>
): AgentSessionRead {
  return {
    id: "chat-1",
    workspace_id: "workspace-1",
    title: "Test chat",
    created_by: "user-1",
    entity_type: "case",
    entity_id: "case-1",
    channel_context: null,
    tools: [],
    agent_preset_id: null,
    agent_preset_version_id: null,
    harness_type: null,
    created_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
    updated_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
    ...overrides,
  }
}

function createSessionReadWithMessages(
  overrides?: Partial<AgentSessionReadWithMessages>
): AgentSessionReadWithMessages {
  return {
    ...createSessionRead(),
    messages: [],
    ...overrides,
  }
}

function createSessionReadVercel(
  overrides?: Partial<AgentSessionReadVercel>
): AgentSessionReadVercel {
  return {
    ...createSessionRead(),
    messages: [],
    ...overrides,
  }
}

describe("useUpdateChat", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    jest.clearAllMocks()
  })

  it("optimistically updates matching chat caches before the mutation resolves", async () => {
    const deferred = createDeferred<AgentSessionRead>()
    mockAgentSessionsUpdateSession.mockImplementation(
      () =>
        deferred.promise as unknown as ReturnType<
          typeof agentSessionsUpdateSession
        >
    )

    queryClient.setQueryData(
      ["chat", "chat-1", "workspace-1"],
      createSessionReadWithMessages()
    )
    queryClient.setQueryData(
      ["chat", "chat-1", "workspace-1", "vercel"],
      createSessionReadVercel()
    )
    queryClient.setQueryData(
      ["chats", "workspace-1", "case", "case-1", 50],
      [createSessionRead()]
    )

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useUpdateChat("workspace-1"), {
      wrapper,
    })

    const mutationPromise = result.current.updateChat({
      chatId: "chat-1",
      update: { tools: ["core.cases.list_cases"] },
    })

    await waitFor(() => {
      expect(
        queryClient.getQueryData<AgentSessionReadWithMessages>([
          "chat",
          "chat-1",
          "workspace-1",
        ])?.tools
      ).toEqual(["core.cases.list_cases"])
    })
    expect(
      queryClient.getQueryData<AgentSessionReadVercel>([
        "chat",
        "chat-1",
        "workspace-1",
        "vercel",
      ])?.tools
    ).toEqual(["core.cases.list_cases"])
    expect(
      queryClient.getQueryData<AgentSessionRead[]>([
        "chats",
        "workspace-1",
        "case",
        "case-1",
        50,
      ])?.[0]?.tools
    ).toEqual(["core.cases.list_cases"])

    deferred.resolve(createSessionRead({ tools: ["core.cases.list_cases"] }))
    await mutationPromise
  })
})
