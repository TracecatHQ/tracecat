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

type AgentSessionReadWithModelSelection = AgentSessionRead & {
  model_catalog_ref?: string | null
}
type AgentSessionReadWithMessagesWithModelSelection =
  AgentSessionReadWithMessages & {
    model_catalog_ref?: string | null
  }
type AgentSessionReadVercelWithModelSelection = AgentSessionReadVercel & {
  model_catalog_ref?: string | null
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

function createSessionRead(
  overrides?: Partial<AgentSessionReadWithModelSelection>
): AgentSessionReadWithModelSelection {
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
    model_catalog_ref: null,
    harness_type: null,
    created_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
    updated_at: new Date("2024-01-01T00:00:00.000Z").toISOString(),
    ...overrides,
  } as AgentSessionReadWithModelSelection
}

function createSessionReadWithMessages(
  overrides?: Partial<AgentSessionReadWithMessagesWithModelSelection>
): AgentSessionReadWithMessagesWithModelSelection {
  return {
    ...createSessionRead(),
    messages: [],
    ...overrides,
  }
}

function createSessionReadVercel(
  overrides?: Partial<AgentSessionReadVercelWithModelSelection>
): AgentSessionReadVercelWithModelSelection {
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
        queryClient.getQueryData<AgentSessionReadWithMessagesWithModelSelection>(
          ["chat", "chat-1", "workspace-1"]
        )?.tools
      ).toEqual(["core.cases.list_cases"])
    })
    expect(
      queryClient.getQueryData<AgentSessionReadVercelWithModelSelection>([
        "chat",
        "chat-1",
        "workspace-1",
        "vercel",
      ])?.tools
    ).toEqual(["core.cases.list_cases"])
    expect(
      queryClient.getQueryData<AgentSessionReadWithModelSelection[]>([
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

  it("optimistically updates preset selection and pinned version caches", async () => {
    const deferred = createDeferred<AgentSessionRead>()
    mockAgentSessionsUpdateSession.mockImplementation(
      () =>
        deferred.promise as unknown as ReturnType<
          typeof agentSessionsUpdateSession
        >
    )

    queryClient.setQueryData(
      ["chat", "chat-1", "workspace-1"],
      createSessionReadWithMessages({
        agent_preset_id: "preset-old",
        agent_preset_version_id: "version-old",
      })
    )
    queryClient.setQueryData(
      ["chat", "chat-1", "workspace-1", "vercel"],
      createSessionReadVercel({
        agent_preset_id: "preset-old",
        agent_preset_version_id: "version-old",
      })
    )
    queryClient.setQueryData(
      ["chats", "workspace-1", "case", "case-1", 50],
      [
        createSessionRead({
          agent_preset_id: "preset-old",
          agent_preset_version_id: "version-old",
        }),
      ]
    )

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useUpdateChat("workspace-1"), {
      wrapper,
    })

    const mutationPromise = result.current.updateChat({
      chatId: "chat-1",
      update: {
        agent_preset_id: "preset-new",
        agent_preset_version_id: "version-new",
      },
    })

    await waitFor(() => {
      expect(
        queryClient.getQueryData<AgentSessionReadWithMessagesWithModelSelection>(
          ["chat", "chat-1", "workspace-1"]
        )
      ).toMatchObject({
        agent_preset_id: "preset-new",
        agent_preset_version_id: "version-new",
      })
    })
    expect(
      queryClient.getQueryData<AgentSessionReadVercelWithModelSelection>([
        "chat",
        "chat-1",
        "workspace-1",
        "vercel",
      ])
    ).toMatchObject({
      agent_preset_id: "preset-new",
      agent_preset_version_id: "version-new",
    })
    expect(
      queryClient.getQueryData<AgentSessionReadWithModelSelection[]>([
        "chats",
        "workspace-1",
        "case",
        "case-1",
        50,
      ])?.[0]
    ).toMatchObject({
      agent_preset_id: "preset-new",
      agent_preset_version_id: "version-new",
    })

    deferred.resolve(
      createSessionRead({
        agent_preset_id: "preset-new",
        agent_preset_version_id: "version-new",
      })
    )
    await mutationPromise
  })

  it("optimistically updates matching chat model selections before the mutation resolves", async () => {
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
      update: { model_catalog_ref: "catalog:source:model" },
    })

    await waitFor(() => {
      expect(
        queryClient.getQueryData<AgentSessionReadWithMessagesWithModelSelection>(
          ["chat", "chat-1", "workspace-1"]
        )?.model_catalog_ref
      ).toBe("catalog:source:model")
    })
    expect(
      queryClient.getQueryData<AgentSessionReadVercelWithModelSelection>([
        "chat",
        "chat-1",
        "workspace-1",
        "vercel",
      ])?.model_catalog_ref
    ).toBe("catalog:source:model")
    expect(
      queryClient.getQueryData<AgentSessionReadWithModelSelection[]>([
        "chats",
        "workspace-1",
        "case",
        "case-1",
        50,
      ])?.[0]?.model_catalog_ref
    ).toBe("catalog:source:model")

    deferred.resolve(
      createSessionRead({
        model_catalog_ref: "catalog:source:model",
      })
    )
    await mutationPromise
  })
})
