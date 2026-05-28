import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { UIMessage } from "ai"
import type {
  AgentSessionRead,
  AgentSessionReadVercel,
  AgentSessionReadWithMessages,
} from "@/client"
import { agentSessionsUpdateSession } from "@/client"
import {
  scanSseIds,
  upsertActivePromptMessage,
  useUpdateChat,
} from "@/hooks/use-chat"

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
    agents_binding: null,
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

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
}

describe("scanSseIds", () => {
  it("publishes the event id after the blank line terminates the SSE event", async () => {
    const lastEventIdRef = { current: null as string | null }

    await scanSseIds(
      streamFromChunks(["id: 1000-0:1\n", 'data: {"type":"text"}\n\n']),
      lastEventIdRef
    )

    expect(lastEventIdRef.current).toBe("1000-0:1")
  })

  it("does not publish an id from an incomplete SSE event", async () => {
    const lastEventIdRef = { current: "999-0:0" as string | null }

    await scanSseIds(
      streamFromChunks(["id: 1000-0:1\n", 'data: {"type":"text"}']),
      lastEventIdRef
    )

    expect(lastEventIdRef.current).toBe("999-0:0")
  })

  it("uses the last id line in a completed SSE event", async () => {
    const lastEventIdRef = { current: null as string | null }

    await scanSseIds(
      streamFromChunks(["id: 1000-0:1\ndata: first\n", "id: 1000-0:2\n\n"]),
      lastEventIdRef
    )

    expect(lastEventIdRef.current).toBe("1000-0:2")
  })
})

describe("upsertActivePromptMessage", () => {
  it("appends the active prompt before the stream creates an assistant", () => {
    const messages: UIMessage[] = []

    const result = upsertActivePromptMessage(messages, {
      chatId: "chat-1",
      currRunId: "run-1",
      prompt: "Investigate this alert",
    })

    expect(result).toEqual([
      {
        id: "active-user:chat-1:run-1",
        role: "user",
        parts: [{ type: "text", text: "Investigate this alert" }],
      },
    ])
  })

  it("inserts the active prompt before an existing streaming assistant", () => {
    const messages: UIMessage[] = [
      {
        id: "chat-1:run-1",
        role: "assistant",
        parts: [{ type: "text", text: "Working" }],
      },
    ]

    const result = upsertActivePromptMessage(messages, {
      chatId: "chat-1",
      currRunId: "run-1",
      prompt: "Investigate this alert",
    })

    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({
      id: "active-user:chat-1:run-1",
      role: "user",
      parts: [{ type: "text", text: "Investigate this alert" }],
    })
    expect(result[1]).toBe(messages[0])
  })

  it("does not duplicate the sender's optimistic user prompt", () => {
    const messages: UIMessage[] = [
      {
        id: "local-user",
        role: "user",
        parts: [{ type: "text", text: "Investigate this alert" }],
      },
      {
        id: "chat-1:run-1",
        role: "assistant",
        parts: [{ type: "text", text: "Working" }],
      },
    ]

    const result = upsertActivePromptMessage(messages, {
      chatId: "chat-1",
      currRunId: "run-1",
      prompt: "Investigate this alert",
    })

    expect(result).toBe(messages)
  })

  it("is idempotent after inserting the active prompt", () => {
    const messages: UIMessage[] = [
      {
        id: "chat-1:run-1",
        role: "assistant",
        parts: [{ type: "text", text: "Working" }],
      },
    ]

    const first = upsertActivePromptMessage(messages, {
      chatId: "chat-1",
      currRunId: "run-1",
      prompt: "Investigate this alert",
    })
    const second = upsertActivePromptMessage(first, {
      chatId: "chat-1",
      currRunId: "run-1",
      prompt: "Investigate this alert",
    })

    expect(second).toBe(first)
  })
})

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
        queryClient.getQueryData<AgentSessionReadWithMessages>([
          "chat",
          "chat-1",
          "workspace-1",
        ])
      ).toMatchObject({
        agent_preset_id: "preset-new",
        agent_preset_version_id: "version-new",
      })
    })
    expect(
      queryClient.getQueryData<AgentSessionReadVercel>([
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
      queryClient.getQueryData<AgentSessionRead[]>([
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
})
