import * as aiSdk from "@ai-sdk/react"
import {
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { DefaultChatTransport, type UIMessage } from "ai"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type AgentSessionCancelResponse,
  type AgentSessionCreate,
  type AgentSessionEntity,
  type AgentSessionRead,
  type AgentSessionStatus,
  type AgentSessionStatusRead,
  type AgentSessionsGetSessionResponse,
  type AgentSessionsGetSessionVercelResponse,
  type AgentSessionsListSessionsResponse,
  type AgentSessionUpdate,
  type ApiError,
  agentSessionsCancelSession,
  agentSessionsCreateSession,
  agentSessionsDeleteSession,
  agentSessionsGetSession,
  agentSessionsGetSessionStatus,
  agentSessionsGetSessionVercel,
  agentSessionsListSessions,
  agentSessionsUpdateSession,
  type ContinueRunRequest,
  type VercelChatRequest,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getBaseUrl } from "@/lib/api"
import { type ModelInfo, toServerUIMessage } from "@/lib/chat"

const DEFAULT_CHAT_ERROR_MESSAGE =
  "The assistant couldn't complete that request. Please try again."

/** A turn still has active run metadata while running or awaiting approval. */
function isActiveTurnStatus(status: AgentSessionStatus | undefined): boolean {
  return status === "running" || status === "waiting_for_approval"
}

/** Fresh stream attachment is only safe while the Redis stream is still open. */
function isStreamAttachableTurnStatus(
  status: AgentSessionStatus | undefined
): boolean {
  return status === "running"
}

function getMessageText(message: UIMessage): string {
  return message.parts
    .filter(
      (part): part is Extract<UIMessage["parts"][number], { type: "text" }> =>
        part.type === "text"
    )
    .map((part) => part.text)
    .join("")
}

function isMatchingUserPrompt(
  message: UIMessage | undefined,
  prompt: string
): boolean {
  return message?.role === "user" && getMessageText(message) === prompt
}

function hasActivePromptMessage(
  messages: UIMessage[],
  promptMessageId: string,
  activeAssistantId: string,
  prompt: string
): boolean {
  if (messages.some((message) => message.id === promptMessageId)) {
    return true
  }

  const activeAssistantIndex = messages.findIndex(
    (message) =>
      message.role === "assistant" && message.id === activeAssistantId
  )
  if (
    activeAssistantIndex > 0 &&
    isMatchingUserPrompt(messages[activeAssistantIndex - 1], prompt)
  ) {
    return true
  }

  return isMatchingUserPrompt(messages[messages.length - 1], prompt)
}

/**
 * Insert the active turn's user prompt into SDK chat state for observer tabs.
 *
 * The sender already gets an optimistic user message from `sendMessage`, while
 * observers only receive the assistant over the Vercel UI stream. This keeps
 * the prompt as durable chat state instead of deriving it during render.
 */
export function upsertActivePromptMessage(
  messages: UIMessage[],
  {
    chatId,
    currRunId,
    prompt,
  }: {
    chatId?: string
    currRunId?: string | null
    prompt?: string | null
  }
): UIMessage[] {
  if (!chatId || !currRunId || !prompt?.trim()) {
    return messages
  }

  const promptMessageId = `active-user:${chatId}:${currRunId}`
  const activeAssistantId = `${chatId}:${currRunId}`

  if (
    hasActivePromptMessage(messages, promptMessageId, activeAssistantId, prompt)
  ) {
    return messages
  }

  const promptMessage: UIMessage = {
    id: promptMessageId,
    role: "user",
    parts: [{ type: "text", text: prompt }],
  }
  const activeAssistantIndex = messages.findIndex(
    (message) =>
      message.role === "assistant" && message.id === activeAssistantId
  )

  if (activeAssistantIndex === -1) {
    return [...messages, promptMessage]
  }

  return [
    ...messages.slice(0, activeAssistantIndex),
    promptMessage,
    ...messages.slice(activeAssistantIndex),
  ]
}

/**
 * Read an SSE response stream and record the latest `id:` line into a ref.
 *
 * The AI SDK does not surface SSE event ids, so we capture them ourselves to
 * resume from the right place on reconnect (sent back as `Last-Event-ID`).
 * Consumes its own tee'd branch of the body; cancels cleanly on stream end.
 */
export async function scanSseIds(
  stream: ReadableStream<Uint8Array>,
  lastEventIdRef: { current: string | null }
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let pendingEventId: string | null = null

  function processLine(rawLine: string) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine
    if (line === "") {
      if (pendingEventId !== null) {
        lastEventIdRef.current = pendingEventId
        pendingEventId = null
      }
      return
    }

    if (line.startsWith("id:")) {
      pendingEventId = line.slice(3).trim()
    }
  }

  function processCompleteLines() {
    let newlineIndex = buffer.indexOf("\n")
    while (newlineIndex !== -1) {
      processLine(buffer.slice(0, newlineIndex))
      buffer = buffer.slice(newlineIndex + 1)
      newlineIndex = buffer.indexOf("\n")
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        processCompleteLines()
        break
      }
      buffer += decoder.decode(value, { stream: true })
      processCompleteLines()
    }
  } catch {
    // Best-effort: a cancelled/aborted stream is not an error for id tracking.
  } finally {
    reader.cancel().catch(() => {})
  }
}

type UpdateableChatRecord =
  | AgentSessionsGetSessionResponse
  | AgentSessionsGetSessionVercelResponse
  | AgentSessionsListSessionsResponse[number]

type UpdateChatContext = {
  previousChat?: AgentSessionsGetSessionResponse
  previousChatVercel?: AgentSessionsGetSessionVercelResponse
  previousChatLists: Array<
    [readonly unknown[], AgentSessionsListSessionsResponse | undefined]
  >
}

function applyOptimisticChatUpdate<T extends UpdateableChatRecord>(
  chat: T,
  update: AgentSessionUpdate
): T {
  return {
    ...chat,
    updated_at: new Date().toISOString(),
    ...(typeof update.title === "string" ? { title: update.title } : {}),
    ...(update.tools !== undefined ? { tools: update.tools ?? [] } : {}),
    ...(update.agent_preset_id !== undefined
      ? { agent_preset_id: update.agent_preset_id }
      : {}),
    ...(update.agent_preset_version_id !== undefined
      ? { agent_preset_version_id: update.agent_preset_version_id }
      : {}),
    ...("harness_type" in chat && update.harness_type !== undefined
      ? { harness_type: update.harness_type }
      : {}),
  }
}

export function parseChatError(error: unknown): string {
  if (!error) {
    return DEFAULT_CHAT_ERROR_MESSAGE
  }

  if (typeof error === "string") {
    return error
  }

  if (error instanceof Response) {
    const statusLine = `${error.status} ${error.statusText || "Unexpected response"}`
    return statusLine.trim()
  }

  if (error instanceof Error) {
    const message = error.message?.trim()
    if (message) {
      try {
        const parsed = JSON.parse(message)
        if (parsed && typeof parsed === "object" && "errorText" in parsed) {
          const parsedMessage = (parsed as { errorText?: unknown }).errorText
          if (typeof parsedMessage === "string" && parsedMessage.trim()) {
            return parsedMessage.trim()
          }
        }
      } catch {
        // message wasn't JSON; fall through to raw text
      }
      return message
    }
  }

  if (typeof error === "object") {
    const candidate = error as {
      errorText?: unknown
      error?: unknown
      message?: unknown
    }
    for (const value of [
      candidate.errorText,
      candidate.error,
      candidate.message,
    ]) {
      if (typeof value === "string" && value.trim()) {
        return value.trim()
      }
    }
  }

  return DEFAULT_CHAT_ERROR_MESSAGE
}

// Hook for creating a new chat
export function useCreateChat(workspaceId: string) {
  const queryClient = useQueryClient()

  const { mutateAsync: createChat, isPending: createChatPending } = useMutation<
    AgentSessionRead,
    ApiError,
    AgentSessionCreate
  >({
    mutationFn: (request: AgentSessionCreate) =>
      agentSessionsCreateSession({ requestBody: request, workspaceId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
    },
  })

  return { createChat, createChatPending }
}

// Hook for listing chats
export function useListChats(
  {
    workspaceId,
    entityType,
    entityId,
    limit = 50,
  }: {
    workspaceId: string
    entityType?: AgentSessionEntity
    entityId?: string
    limit?: number
  },
  options?: { enabled?: boolean }
): {
  chats: AgentSessionsListSessionsResponse | undefined
  chatsLoading: boolean
  chatsError: ApiError | null
  refetchChats: UseQueryResult<
    AgentSessionsListSessionsResponse,
    ApiError
  >["refetch"]
} {
  const {
    data: chats,
    isLoading: chatsLoading,
    error: chatsError,
    refetch,
  } = useQuery<AgentSessionsListSessionsResponse, ApiError>({
    queryKey: ["chats", workspaceId, entityType, entityId, limit],
    queryFn: () =>
      agentSessionsListSessions({
        workspaceId,
        entityType: entityType || null,
        entityId: entityId || null,
        limit,
      }),
    enabled: options?.enabled ?? true,
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return { chats, chatsLoading, chatsError, refetchChats: refetch }
}

// Hook for getting a single chat
export function useGetChat({
  chatId,
  workspaceId,
}: {
  chatId: string
  workspaceId: string
}) {
  const {
    data: chat,
    isLoading,
    error,
  } = useQuery<AgentSessionsGetSessionResponse, ApiError>({
    queryKey: ["chat", chatId, workspaceId],
    queryFn: () => agentSessionsGetSession({ sessionId: chatId, workspaceId }),
    enabled: !!chatId && !!workspaceId,
  })

  return { chat, isLoading, error }
}

// Hook for updating a chat
export function useUpdateChat(workspaceId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation<
    AgentSessionRead,
    ApiError,
    { chatId: string; update: AgentSessionUpdate },
    UpdateChatContext
  >({
    mutationFn: ({ chatId, update }) =>
      agentSessionsUpdateSession({
        sessionId: chatId,
        workspaceId,
        requestBody: update,
      }),
    onMutate: async ({ chatId, update }) => {
      await Promise.all([
        queryClient.cancelQueries({
          queryKey: ["chat", chatId, workspaceId],
        }),
        queryClient.cancelQueries({
          queryKey: ["chat", chatId, workspaceId, "vercel"],
        }),
        queryClient.cancelQueries({
          queryKey: ["chats", workspaceId],
        }),
      ])

      const previousChat =
        queryClient.getQueryData<AgentSessionsGetSessionResponse>([
          "chat",
          chatId,
          workspaceId,
        ])
      const previousChatVercel =
        queryClient.getQueryData<AgentSessionsGetSessionVercelResponse>([
          "chat",
          chatId,
          workspaceId,
          "vercel",
        ])
      const previousChatLists =
        queryClient.getQueriesData<AgentSessionsListSessionsResponse>({
          queryKey: ["chats", workspaceId],
        })

      queryClient.setQueryData<AgentSessionsGetSessionResponse>(
        ["chat", chatId, workspaceId],
        (current) =>
          current ? applyOptimisticChatUpdate(current, update) : current
      )
      queryClient.setQueryData<AgentSessionsGetSessionVercelResponse>(
        ["chat", chatId, workspaceId, "vercel"],
        (current) =>
          current ? applyOptimisticChatUpdate(current, update) : current
      )

      for (const [queryKey] of previousChatLists) {
        queryClient.setQueryData<AgentSessionsListSessionsResponse>(
          queryKey,
          (current) =>
            current?.map((chatRecord) =>
              chatRecord.id === chatId
                ? applyOptimisticChatUpdate(chatRecord, update)
                : chatRecord
            ) ?? current
        )
      }

      return {
        previousChat,
        previousChatVercel,
        previousChatLists,
      }
    },
    onError: (_, variables, context) => {
      if (!context) {
        return
      }

      queryClient.setQueryData(
        ["chat", variables.chatId, workspaceId],
        context.previousChat
      )
      queryClient.setQueryData(
        ["chat", variables.chatId, workspaceId, "vercel"],
        context.previousChatVercel
      )
      for (const [queryKey, previousData] of context.previousChatLists) {
        queryClient.setQueryData(queryKey, previousData)
      }
    },
    onSettled: (_, __, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.chatId, workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.chatId, workspaceId, "vercel"],
      })
      queryClient.invalidateQueries({
        queryKey: ["chats", workspaceId],
      })
    },
  })

  return {
    updateChat: mutation.mutateAsync,
    isUpdating: mutation.isPending,
    updateError: mutation.error,
  }
}

// Hook for deleting a chat
export function useDeleteChat(workspaceId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation<void, ApiError, { chatId: string }>({
    mutationFn: ({ chatId }) =>
      agentSessionsDeleteSession({
        sessionId: chatId,
        workspaceId,
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.chatId, workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.chatId, workspaceId, "vercel"],
      })
      queryClient.invalidateQueries({
        queryKey: ["chats", workspaceId],
      })
    },
  })

  return {
    deleteChat: mutation.mutateAsync,
    isDeleting: mutation.isPending,
    deleteError: mutation.error,
  }
}

/** Request cancellation for the currently running agent turn. */
export function useCancelChatTurn(workspaceId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation<
    AgentSessionCancelResponse,
    ApiError,
    { chatId: string }
  >({
    mutationFn: ({ chatId }) =>
      agentSessionsCancelSession({
        sessionId: chatId,
        workspaceId,
        requestBody: { reason: "user_cancel" },
      }),
    onSettled: (_, __, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.chatId, workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.chatId, workspaceId, "vercel"],
      })
      queryClient.invalidateQueries({
        queryKey: ["chats", workspaceId],
      })
    },
  })

  return {
    cancelChatTurn: mutation.mutateAsync,
    isCancellingChatTurn: mutation.isPending,
    cancelChatTurnError: mutation.error,
  }
}

export function useGetChatVercel({
  chatId,
  workspaceId,
}: {
  chatId?: string
  workspaceId: string
}) {
  const {
    data: chat,
    isLoading: chatLoading,
    error: chatError,
  } = useQuery<AgentSessionsGetSessionVercelResponse, ApiError>({
    queryKey: ["chat", chatId, workspaceId, "vercel"],
    queryFn: async () => {
      if (!chatId) {
        throw new Error("No chat ID available")
      }
      return await agentSessionsGetSessionVercel({
        sessionId: chatId,
        workspaceId,
      })
    },
    enabled: !!chatId,
  })
  return { chat, chatLoading, chatError }
}

/**
 * Poll the lifecycle status endpoint so a client learns when a turn
 * starts (e.g. from another tab) and can attach to the live stream. Kept
 * separate from the heavy message-history fetch. Polls a touch faster while a
 * turn is live; always polls so an idle client notices a new turn.
 */
export function useSessionStatus({
  chatId,
  workspaceId,
}: {
  chatId?: string
  workspaceId: string
}) {
  const { data } = useQuery<AgentSessionStatusRead, ApiError>({
    queryKey: ["chat", chatId, workspaceId, "status"],
    queryFn: async () => {
      if (!chatId) {
        throw new Error("No chat ID available")
      }
      return await agentSessionsGetSessionStatus({
        sessionId: chatId,
        workspaceId,
      })
    },
    enabled: !!chatId,
    refetchInterval: (query) =>
      isActiveTurnStatus(query.state.data?.turn_status) ? 2000 : 3000,
    refetchIntervalInBackground: false,
  })
  return {
    turnStatus: data?.turn_status,
    currRunId: data?.curr_run_id,
    prompt: data?.prompt,
  }
}

// Combined hook for chat functionality with Vercel AI SDK streaming
export function useVercelChat({
  chatId,
  workspaceId,
  messages,
  modelInfo,
  turnStatus,
  currRunId,
  activePrompt,
}: {
  chatId?: string
  workspaceId: string
  messages: UIMessage[]
  modelInfo: ModelInfo
  /** Server-reported lifecycle status; drives attaching to a live turn. */
  turnStatus?: AgentSessionStatus
  /** Active server run id; used to key the observer prompt bubble. */
  currRunId?: string | null
  /** Active run's user prompt for observer tabs. */
  activePrompt?: string | null
}) {
  const queryClient = useQueryClient()
  const [lastError, setLastError] = useState<string | null>(null)
  // Last SSE id seen on the live stream; resent as Last-Event-ID on reconnect.
  const lastEventIdRef = useRef<string | null>(null)
  const resumeAttemptKeyRef = useRef<string | null>(null)
  const activeResumeRunKeyRef = useRef<string | null>(null)
  const completedResumeRunKeyRef = useRef<string | null>(null)
  const insertedPromptKeyRef = useRef<string | null>(null)
  const previousActiveRunRef = useRef<{
    currRunId?: string | null
    turnStatus?: AgentSessionStatus
  }>({})

  // Tee every streamed response: one branch feeds the SDK untouched, the other
  // is scanned for SSE `id:` lines (the SDK does not expose them).
  const trackingFetch = useCallback<typeof fetch>(async (input, init) => {
    const response = await fetch(input, init)
    if (!response.body) return response
    const [toSdk, toScan] = response.body.tee()
    void scanSseIds(toScan, lastEventIdRef)
    return new Response(toSdk, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    })
  }, [])

  // Build the Vercel streaming endpoint URL
  const apiEndpoint = useMemo(() => {
    if (!chatId) return ""
    const url = new URL(`/api/agent/sessions/${chatId}/messages`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)
    return url.toString()
  }, [chatId, workspaceId])

  // Use Vercel's useChat hook for streaming
  // We attach to live turns via the status-driven effect below, not the SDK's
  // one-shot `resume` (fires once on mount, never re-fires for a turn started
  // afterward by another client).
  const chat = aiSdk.useChat({
    id: chatId,
    messages,
    transport: new DefaultChatTransport({
      api: apiEndpoint,
      credentials: "include",
      fetch: trackingFetch,
      prepareReconnectToStreamRequest: ({ id }) => {
        const url = new URL(`/api/agent/sessions/${id}/stream`, getBaseUrl())
        url.searchParams.set("workspace_id", workspaceId)
        const headers: Record<string, string> = {}
        if (lastEventIdRef.current) {
          headers["Last-Event-ID"] = lastEventIdRef.current
        }
        return {
          api: url.toString(),
          credentials: "include",
          headers,
        }
      },
      prepareSendMessagesRequest: ({ messages }) => {
        // Support both normal vercel chat turns and approval continuations.
        const last = messages[messages.length - 1]
        const dataPart = last.parts.find((p) => p.type === "data-continue") as
          | { type: "data-continue"; data?: ContinueRunRequest }
          | undefined

        if (dataPart?.data?.decisions) {
          const body: ContinueRunRequest = {
            kind: "continue",
            decisions: dataPart.data.decisions,
            source: dataPart.data.source ?? "inbox",
          }
          return { body }
        }

        // Default: start a vercel chat turn with the last UI message
        const body: VercelChatRequest = {
          kind: "vercel",
          model: modelInfo?.name,
          model_provider: modelInfo?.provider,
          message: toServerUIMessage(last),
        }
        const baseUrl = (modelInfo as { baseUrl?: string | null })?.baseUrl
        if (baseUrl != null) body.base_url = baseUrl
        return { body }
      },
    }),
    onError: (error) => {
      const friendlyMessage = parseChatError(error)
      setLastError(friendlyMessage)
      console.error("Error in Vercel chat:", error)
      toast({
        title: "Chat error",
        description: friendlyMessage,
      })
    },
    onFinish: ({ isAbort, isDisconnect, isError }) => {
      if (activeResumeRunKeyRef.current) {
        if (isAbort || isDisconnect || isError) {
          resumeAttemptKeyRef.current = null
        } else {
          completedResumeRunKeyRef.current = activeResumeRunKeyRef.current
        }
        activeResumeRunKeyRef.current = null
      }
      setLastError(null)
      queryClient.invalidateQueries({
        queryKey: ["chat", chatId, workspaceId, "vercel"],
      })
      queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
    },
  })

  const { messages: chatMessages, status, resumeStream, setMessages } = chat
  const activePromptText = activePrompt?.trim() ? activePrompt : undefined
  const activePromptPresent =
    chatId && currRunId && activePromptText
      ? hasActivePromptMessage(
          chatMessages,
          `active-user:${chatId}:${currRunId}`,
          `${chatId}:${currRunId}`,
          activePromptText
        )
      : false

  useEffect(() => {
    const previous = previousActiveRunRef.current
    if (
      previous.currRunId !== currRunId ||
      !isActiveTurnStatus(turnStatus) ||
      (previous.turnStatus === "waiting_for_approval" &&
        turnStatus === "running")
    ) {
      resumeAttemptKeyRef.current = null
      activeResumeRunKeyRef.current = null
      completedResumeRunKeyRef.current = null
      insertedPromptKeyRef.current = null
    }

    previousActiveRunRef.current = { currRunId, turnStatus }
  }, [currRunId, turnStatus])

  // Observer tabs don't call sendMessage, so they don't get the optimistic user
  // bubble. Add it to useChat state from the status poll before attaching.
  useEffect(() => {
    if (
      !isStreamAttachableTurnStatus(turnStatus) ||
      status !== "ready" ||
      !chatId ||
      !currRunId ||
      !activePromptText ||
      activePromptPresent
    ) {
      return
    }

    const promptInsertKey = `${chatId}:${currRunId}`
    if (insertedPromptKeyRef.current === promptInsertKey) {
      return
    }

    insertedPromptKeyRef.current = promptInsertKey
    setMessages((current) =>
      upsertActivePromptMessage(current, {
        chatId,
        currRunId,
        prompt: activePromptText,
      })
    )
  }, [
    activePromptPresent,
    activePromptText,
    chatId,
    currRunId,
    setMessages,
    status,
    turnStatus,
  ])

  // Attach to a live turn whenever the server reports one and we're idle.
  // The condition self-guards: resumeStream() flips status off "ready", so it
  // won't re-fire mid-stream; if the stream drops while the turn is still
  // running, the next status poll re-attaches. Handles back-to-back turns
  // started by another client without relying on the SDK's one-shot `resume`.
  useEffect(() => {
    if (!isStreamAttachableTurnStatus(turnStatus) || status !== "ready") {
      return
    }

    if (activePromptText && !activePromptPresent) {
      return
    }

    const resumeRunKey =
      chatId && currRunId ? `${chatId}:${currRunId}` : undefined
    if (!resumeRunKey || completedResumeRunKeyRef.current === resumeRunKey) {
      return
    }

    const resumeAttemptKey = `${resumeRunKey}:${lastEventIdRef.current ?? "0-0"}`
    if (resumeAttemptKeyRef.current === resumeAttemptKey) {
      return
    }

    resumeAttemptKeyRef.current = resumeAttemptKey
    activeResumeRunKeyRef.current = resumeRunKey
    void resumeStream()
  }, [
    activePromptPresent,
    activePromptText,
    chatId,
    currRunId,
    resumeStream,
    status,
    turnStatus,
  ])

  return {
    ...chat,
    lastError,
    clearError: useCallback(() => setLastError(null), []),
  }
}

// --- Approvals helpers (CE handshake) ----------------------------------------

export type ApprovalCard = {
  tool_call_id: string
  tool_name: string
  args?: unknown
}

/**
 * Build a synthetic UIMessage that encodes a continue request with approval decisions.
 * Pass this message to your chat messages array before sending.
 */
export function makeContinueMessage(
  decisions: ContinueRunRequest["decisions"],
  source: ContinueRunRequest["source"] = "inbox"
): UIMessage {
  return {
    id: `continue-${Date.now()}`,
    role: "user",
    parts: [
      {
        type: "data-continue",
        data: { kind: "continue", source, decisions },
      } as UIMessage["parts"][number],
    ],
  }
}
