import * as aiSdk from "@ai-sdk/react"
import type { ChatOnDataCallback, ChatStatus, UIMessage } from "ai"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type AgentBackendRead,
  type AgentControlReceipt,
  type AgentPendingMessageRead,
  type AgentSessionCreate,
  type AgentSessionEntity,
  type AgentSessionRead,
  type AgentSessionsCancelSessionResponse,
  type AgentSessionsGetSessionResponse,
  type AgentSessionsGetSessionVercelResponse,
  type AgentSessionsListPendingMessagesResponse,
  type AgentSessionsListSessionsResponse,
  type AgentSessionsRemoveSessionArtifactData,
  type AgentSessionsRemoveSessionArtifactResponse,
  type AgentSessionUpdate,
  type ApiError,
  agentSessionsCancelSession,
  agentSessionsControlSession,
  agentSessionsCreateSession,
  agentSessionsDeleteSession,
  agentSessionsGetSession,
  agentSessionsGetSessionVercel,
  agentSessionsListAgentBackends,
  agentSessionsListPendingMessages,
  agentSessionsListSessions,
  agentSessionsRemoveSessionArtifact,
  agentSessionsUpdateSession,
  type ChatPageContext,
  type ContinueRunRequest,
  type VercelChatRequest,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getBaseUrl } from "@/lib/api"
import {
  CHAT_MESSAGES_THROTTLE_MS,
  CoalescingChatTransport,
  isUserPrompt,
  type ModelInfo,
  toServerUIMessage,
} from "@/lib/chat"
import {
  chatStreamUrl,
  readDroppedPrompt,
  refetchChatTranscript,
  saveDroppedPrompt,
  serverTookPrompt,
} from "@/lib/chat-dropped-prompt"
import type { WithdrawPendingRequest } from "@/lib/extensions"
import {
  type QueryClient,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"
import {
  AGENT_CHUNK_DATA_PART_TYPE,
  parseAgentChunkData,
  SubagentStreamStore,
} from "@/lib/subagent-stream"

const DEFAULT_CHAT_ERROR_MESSAGE =
  "The assistant couldn't complete that request. Please try again."

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

type RemoveSessionArtifactInput = Omit<
  AgentSessionsRemoveSessionArtifactData,
  "workspaceId"
>

function applyOptimisticChatUpdate<T extends UpdateableChatRecord>(
  chat: T,
  update: AgentSessionUpdate
): T {
  return {
    ...chat,
    updated_at: new Date().toISOString(),
    ...(typeof update.title === "string" ? { title: update.title } : {}),
    ...(update.tools !== undefined ? { tools: update.tools ?? [] } : {}),
    ...(update.mcp_integrations !== undefined
      ? { mcp_integrations: update.mcp_integrations ?? [] }
      : {}),
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

/**
 * Extract an actionable message from a FastAPI validation error (HTTP 422).
 *
 * These surface as an `ApiError` whose `message` is just "Validation Error",
 * so we reach into the response body's `detail` to explain what actually
 * failed. Field limits such as the per-chat tool cap get a friendly message;
 * everything else falls back to Pydantic's own `msg` text. Returns `null` when
 * the error isn't a recognizable 422 payload.
 */
function parseValidationError(error: unknown): string | null {
  if (!error || typeof error !== "object") {
    return null
  }

  const { status, body } = error as { status?: number; body?: unknown }
  if (status !== 422 || !body || typeof body !== "object") {
    return null
  }

  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim()
  }
  if (!Array.isArray(detail)) {
    return null
  }

  const messages: string[] = []
  for (const item of detail) {
    if (!item || typeof item !== "object") {
      continue
    }
    const loc = (item as { loc?: unknown }).loc
    const field = Array.isArray(loc)
      ? loc.filter((part) => typeof part === "string" && part !== "body").pop()
      : undefined
    const maxLength = (item as { ctx?: { max_length?: unknown } }).ctx
      ?.max_length
    if (
      (field === "tools" || field === "mcp_integrations") &&
      typeof maxLength === "number"
    ) {
      const noun = field === "tools" ? "tools" : "MCP integrations"
      messages.push(
        `You can add at most ${maxLength} ${noun}. Remove some and try again.`
      )
      continue
    }
    const msg = (item as { msg?: unknown }).msg
    if (typeof msg === "string" && msg.trim()) {
      messages.push(msg.trim())
    }
  }

  return messages.length > 0 ? messages.join(" ") : null
}

export function parseChatError(error: unknown): string {
  if (!error) {
    return DEFAULT_CHAT_ERROR_MESSAGE
  }

  const validationMessage = parseValidationError(error)
  if (validationMessage) {
    return validationMessage
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
        // A rejected send carries the API's `{"detail": "..."}` body as its message.
        if (parsed && typeof parsed === "object" && "detail" in parsed) {
          const detail = (parsed as { detail?: unknown }).detail
          if (typeof detail === "string" && detail.trim()) {
            return detail.trim()
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
    createdBy,
    limit = 50,
  }: {
    workspaceId: string
    entityType?: AgentSessionEntity
    entityId?: string
    createdBy?: string
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
    queryKey: ["chats", workspaceId, entityType, entityId, createdBy, limit],
    queryFn: () =>
      agentSessionsListSessions({
        workspaceId,
        entityType: entityType || null,
        entityId: entityId || null,
        createdBy: createdBy || null,
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
    meta: { suppressErrorToast: false },
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

const NO_PENDING_MESSAGES: AgentPendingMessageRead[] = []

/**
 * The user's messages the model has not read yet, in send order, and a way to
 * withdraw them.
 *
 * Fetches only while `enabled`; otherwise the list is empty.
 */
export function usePendingMessages({
  workspaceId,
  chatId,
  enabled,
}: {
  workspaceId: string
  chatId?: string
  enabled: boolean
}) {
  const queryClient = useQueryClient()
  const active = enabled && !!chatId
  const { data } = useQuery<AgentSessionsListPendingMessagesResponse, ApiError>(
    {
      queryKey: ["chat", chatId, workspaceId, "pending"],
      queryFn: () =>
        agentSessionsListPendingMessages({
          sessionId: chatId ?? "",
          workspaceId,
        }),
      enabled: active,
      refetchOnWindowFocus: false,
    }
  )
  const refreshPending = useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: ["chat", chatId, workspaceId, "pending"],
    })
  }, [chatId, queryClient, workspaceId])

  const withdraw = useCallback(
    async ({
      id,
      targetIds,
    }: WithdrawPendingRequest): Promise<AgentControlReceipt> => {
      if (!chatId) {
        throw new Error("No chat to withdraw from")
      }
      try {
        return await agentSessionsControlSession({
          sessionId: chatId,
          workspaceId,
          requestBody: { type: "withdraw", id, target_ids: targetIds ?? null },
        })
      } finally {
        await refreshPending()
      }
    },
    [chatId, refreshPending, workspaceId]
  )

  return {
    pending: active && data ? data.items : NO_PENDING_MESSAGES,
    refreshPending,
    withdraw,
  }
}

/** Request cancellation for the currently running agent turn. */
export function useCancelChatTurn(workspaceId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation<
    AgentSessionsCancelSessionResponse,
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
  refetchInterval,
  enabled = true,
  retry,
}: {
  chatId?: string
  workspaceId: string
  refetchInterval?: number | false
  enabled?: boolean
  retry?: boolean
}) {
  const {
    data: chat,
    isLoading: chatLoading,
    isFetching: chatFetching,
    error: chatError,
    refetch: refetchChat,
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
    enabled: !!chatId && enabled,
    ...(retry !== undefined ? { retry } : {}),
    // A remount must never render a stale cache snapshot as the final
    // transcript: always refetch so the pane adopts the current server copy
    // (e.g. after an approval was resolved from another surface).
    refetchOnMount: "always",
    refetchInterval,
  })
  return { chat, chatLoading, chatFetching, chatError, refetchChat }
}

function applyArtifactsToVercelChat(
  chat: AgentSessionsGetSessionVercelResponse | undefined,
  response: AgentSessionsRemoveSessionArtifactResponse
): AgentSessionsGetSessionVercelResponse | undefined {
  if (!chat || !("artifacts" in chat)) {
    return chat
  }
  return {
    ...chat,
    artifacts: response.artifacts ?? [],
  }
}

export function useRemoveSessionArtifact(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (input: RemoveSessionArtifactInput) =>
      await agentSessionsRemoveSessionArtifact({
        ...input,
        workspaceId,
      }),
    onSuccess: (response, variables) => {
      queryClient.setQueryData<
        AgentSessionsGetSessionVercelResponse | undefined
      >(["chat", variables.sessionId, workspaceId, "vercel"], (current) =>
        applyArtifactsToVercelChat(current, response)
      )
      queryClient.invalidateQueries({
        queryKey: ["chat", variables.sessionId, workspaceId, "vercel"],
      })
    },
  })

  return {
    removeArtifact: mutation.mutateAsync,
    isRemovingArtifact: mutation.isPending,
    removeArtifactError: mutation.error,
  }
}

/**
 * Refresh all queries whose data can change when a chat turn finishes.
 */
export function invalidateChatTurnQueries(
  queryClient: QueryClient,
  {
    chatId,
    workspaceId,
  }: {
    chatId?: string
    workspaceId: string
  }
) {
  queryClient.invalidateQueries({
    queryKey: ["chat", chatId, workspaceId, "vercel"],
  })
  queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
  // A completed turn can change a session's inbox status (e.g. an approval
  // continuation moves a row from "Review required" to "Completed"). The inbox
  // detail pane derives its status/approval state from these queries, so refresh
  // them here too. Both are already polled, so this just makes the update
  // prompt; it is a no-op when no inbox view is mounted to observe them.
  queryClient.invalidateQueries({ queryKey: ["inbox-items"] })
  queryClient.invalidateQueries({
    queryKey: ["pending-approvals-count", workspaceId],
  })
}

// Combined hook for chat functionality with Vercel AI SDK streaming
export function useVercelChat({
  chatId,
  workspaceId,
  messages,
  modelInfo,
  onData,
  pageContext,
  resume = true,
}: {
  chatId?: string
  workspaceId: string
  messages: UIMessage[]
  modelInfo?: ModelInfo
  onData?: ChatOnDataCallback<UIMessage>
  /** The object the page around a docked chat shows, sent with each turn. */
  pageContext?: ChatPageContext | null
  /**
   * Reconnect to the live event stream on mount. Defaults to true. Set to
   * false for terminal sessions whose history is already seeded from the DB:
   * resuming there replays the last persisted turn on top of the seeded
   * messages, duplicating the chat.
   */
  resume?: boolean
}) {
  const queryClient = useQueryClient()
  // Reconnecting after a drop needs a fresh SDK Chat: the old one keeps the cut
  // stream's partial state, and the server's full replay would duplicate it.
  const [reconnect, setReconnect] = useState<{
    chatId: string
    generation: number
  } | null>(null)
  const generation =
    reconnect && reconnect.chatId === chatId ? reconnect.generation : 0
  const chatKey = chatId && `${chatId}#${generation}`
  const [lastError, setLastError] = useState<{
    chatKey: string | undefined
    message: string
    disconnected: boolean
    /** The drop cut a reconnect replay, which never shows its turn's prompt. */
    replay?: boolean
  } | null>(null)
  // useChat reads its seed once per chat, so the dropped prompt is read once too.
  const droppedPromptRef = useRef<{
    chatKey: string | undefined
    prompt: UIMessage | null
  } | null>(null)
  if (
    !droppedPromptRef.current ||
    droppedPromptRef.current.chatKey !== chatKey
  ) {
    droppedPromptRef.current = {
      chatKey,
      prompt: readDroppedPrompt(chatId, messages),
    }
  }
  const droppedPrompt = droppedPromptRef.current.prompt
  // A pane reused across chats must not resurface a chat's old live error;
  // only a dropped prompt the server never got comes back.
  useEffect(() => {
    setLastError(
      droppedPromptRef.current?.prompt
        ? { chatKey, message: "Connection dropped", disconnected: true }
        : null
    )
  }, [chatKey])
  // Whether the request in flight is this tab's send or a reconnect replay.
  const requestKindRef = useRef<"send" | "resume">("send")
  // A replay's prompt is off screen, so its turn is never resent. A Retry
  // reconnect means the server took the turn; a restored prompt is on screen.
  function isReplay(): boolean {
    return (
      requestKindRef.current === "resume" &&
      (generation > 0 || !droppedPromptRef.current?.prompt)
    )
  }
  // Live child-session transcripts. Child chunks are transient data parts, so
  // they never enter `messages`; they are routed here instead.
  const subagentStore = useMemo(
    () => new SubagentStreamStore(),
    [chatKey, workspaceId]
  )
  useEffect(() => () => subagentStore.dispose(), [subagentStore])

  // Build the Vercel streaming endpoint URL
  const apiEndpoint = useMemo(() => {
    if (!chatId) return ""
    const url = new URL(`/api/agent/sessions/${chatId}/messages`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)
    return url.toString()
  }, [chatId, workspaceId])

  // The AI SDK builds its transport once per chat id and ignores later ones, so
  // a closure over `pageContext` would keep the page the chat first rendered
  // on. Read it through a ref when the request is built instead.
  const pageContextRef = useRef(pageContext)
  pageContextRef.current = pageContext

  // Use Vercel's useChat hook for streaming. On reconnect the server replays the
  // whole active turn from the start (it hides the active run's DB rows mid-turn,
  // so Redis is the sole source for the live assistant); we therefore do not send
  // a Last-Event-ID cursor.
  const chat = aiSdk.useChat({
    id: chatKey,
    // Reconnects are started below so sends can wait for them.
    resume: false,
    messages: droppedPrompt ? [...messages, droppedPrompt] : messages,
    experimental_throttle: CHAT_MESSAGES_THROTTLE_MS,
    transport: new CoalescingChatTransport({
      api: apiEndpoint,
      credentials: "include",
      prepareReconnectToStreamRequest: () => {
        if (!chatId) throw new Error("Reconnecting needs a chat id")
        return {
          api: chatStreamUrl(chatId, workspaceId),
          credentials: "include",
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
        const currentPageContext = pageContextRef.current
        if (currentPageContext) body.page_context = currentPageContext
        return { body }
      },
    }),
    onError: (error) => {
      const friendlyMessage = parseChatError(error)
      // A reconnect that fails before its stream opens gets no onFinish and
      // leaves status untouched; the turn is unknown, so mark it dropped.
      const streamOpened =
        statusRef.current === "submitted" || statusRef.current === "streaming"
      const reconnectDropped =
        requestKindRef.current === "resume" &&
        (generation > 0 || error instanceof TypeError || !streamOpened)
      setLastError({
        chatKey,
        message: friendlyMessage,
        disconnected: reconnectDropped,
        replay: reconnectDropped && isReplay(),
      })
      console.error("Error in Vercel chat:", error)
      toast({
        title: "Chat error",
        description: friendlyMessage,
      })
    },
    onFinish: ({ messages: turnMessages, isAbort, isDisconnect, isError }) => {
      if (!isAbort && !isDisconnect && !isError) {
        void subagentStore.complete()
      }
      // Leaving a chat aborts its request, and the SDK runs that abort's
      // onFinish with the next chat's callbacks; sends already cleared state.
      if (!isAbort) {
        if (!isError) {
          setLastError((prev) => (prev?.chatKey === chatKey ? null : prev))
        } else if (isDisconnect) {
          // The SDK also runs onFinish after onError; keep that turn's error.
          const replay = isReplay()
          setLastError(
            (prev) => prev && { ...prev, disconnected: true, replay }
          )
        }
        // Sends already cleared the saved prompt, and a reconnect's history may
        // still lack it, so only a dropped send saves one.
        if (chatId && isDisconnect && requestKindRef.current === "send") {
          saveDroppedPrompt(chatId, turnMessages)
        }
      }
      invalidateChatTurnQueries(queryClient, { chatId, workspaceId })
      // First-prompt auto-titling runs as a detached backend task that can
      // commit after the immediate invalidation on fast turns, leaving the
      // placeholder title ("Chat 1", ...) in the sidebar until the next
      // mutation. Re-check on a delay — the second point covers the titling
      // LLM call's 15s timeout. No-op when nothing is stale or mounted.
      for (const delayMs of [4_000, 16_000]) {
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
        }, delayMs)
      }
    },
    onData: (dataPart) => {
      if (dataPart.type === AGENT_CHUNK_DATA_PART_TYPE) {
        const chunkData = parseAgentChunkData(dataPart.data)
        if (chunkData) {
          subagentStore.ingest(chunkData)
        }
        return
      }
      onData?.(dataPart)
    },
  })

  // The SDK runs one request per chat at a time. Overlapping requests share
  // its active-response slot: the first to settle clears the slot after
  // running onFinish early for the other's turn, and the second then throws
  // reading it ("Cannot read properties of undefined (reading 'state')") and
  // skips its own onFinish. A send on mount, such as a new chat's pending
  // first message, would overlap the mount reconnect, so sends wait for it.
  const { resumeStream, sendMessage, regenerate, status } = chat
  const pendingResumeRef = useRef<{
    chatKey: string
    done: Promise<void>
  } | null>(null)
  const statusRef = useRef(status)
  statusRef.current = status
  // A chat Retry reconnected resumes even when `resume` is off.
  const shouldResume = Boolean(chatKey) && (resume || generation > 0)
  useEffect(() => {
    if (!shouldResume || !chatKey) return
    // A strict-mode re-run reuses the reconnect already in flight, and a
    // request already in flight owns the turn it would reconnect to.
    if (pendingResumeRef.current?.chatKey === chatKey) return
    if (statusRef.current === "submitted" || statusRef.current === "streaming")
      return
    requestKindRef.current = "resume"
    const done = resumeStream().finally(() => {
      if (pendingResumeRef.current?.done === done) {
        pendingResumeRef.current = null
      }
    })
    pendingResumeRef.current = { chatKey, done }
  }, [chatKey, shouldResume, resumeStream])
  const waitForResume = useCallback(async () => {
    const pending = pendingResumeRef.current
    if (pending && pending.chatKey === chatKey) await pending.done
  }, [chatKey])
  const startTurn = useCallback(() => {
    requestKindRef.current = "send"
    setLastError(null)
    if (chatId) saveDroppedPrompt(chatId, null)
  }, [chatId])
  const sendMessageAfterResume = useCallback<typeof sendMessage>(
    async (...args) => {
      await waitForResume()
      startTurn()
      await sendMessage(...args)
    },
    [sendMessage, startTurn, waitForResume]
  )
  const regenerateAfterResume = useCallback<typeof regenerate>(
    async (...args) => {
      await waitForResume()
      startTurn()
      await regenerate(...args)
    },
    [regenerate, startTurn, waitForResume]
  )

  const currentError = lastError?.chatKey === chatKey ? lastError : null
  const { messages: liveMessages } = chat
  /**
   * Retries the turn `messageId` started. After a dropped connection whose
   * prompt the server took, reconnects to that turn instead of starting a
   * second run. Throws, leaving the error up, when the server can't be checked.
   */
  const retryTurn = useCallback(
    async (messageId: string | undefined): Promise<void> => {
      const prompt = liveMessages.find((m) => m.id === messageId)
      // A replay's prompt is off screen, and the transcript never stores an
      // approval continuation, so neither is resent: adopt the server's state.
      const unconfirmable =
        currentError?.replay || (prompt !== undefined && !isUserPrompt(prompt))
      if (currentError?.disconnected && chatId && unconfirmable) {
        await refetchChatTranscript(queryClient, chatId, workspaceId)
        setReconnect({ chatId, generation: generation + 1 })
        return
      }
      if (currentError?.disconnected && chatId && prompt) {
        const took = await serverTookPrompt({
          chatId,
          workspaceId,
          prompt,
          refetchTranscript: () =>
            refetchChatTranscript(queryClient, chatId, workspaceId),
        })
        if (took) {
          setReconnect({ chatId, generation: generation + 1 })
          return
        }
      }
      await regenerateAfterResume({ messageId })
    },
    [
      chatId,
      currentError,
      generation,
      liveMessages,
      queryClient,
      regenerateAfterResume,
      workspaceId,
    ]
  )

  return {
    ...chat,
    sendMessage: sendMessageAfterResume,
    regenerate: regenerateAfterResume,
    retryTurn,
    subagentStore,
    lastError: currentError?.message ?? null,
    /** The last error cut the connection; the server-side turn may still run. */
    lastErrorDisconnected: currentError?.disconnected ?? false,
    clearError: useCallback(() => setLastError(null), []),
  }
}

/**
 * Identity signature of a transcript: message ids plus the id, type, state, and
 * text of each part, and approval card payloads. Two transcripts with the same
 * signature carry the same rendered content, so an unchanged signature means
 * there is nothing to adopt.
 */
function transcriptSignature(messages: UIMessage[]): string {
  return JSON.stringify(
    messages.map((m) => [
      m.id,
      m.parts.map((p) => [
        p.type,
        "state" in p ? p.state : undefined,
        "text" in p ? p.text : undefined,
        // A decision made elsewhere changes only the card's payload.
        p.type === "data-approval-request" && "data" in p ? p.data : undefined,
      ]),
    ])
  )
}

/** A message whose parts are all approval-request cards (nothing else). */
function isApprovalCardMessage(m: UIMessage): boolean {
  return (
    m.parts.length > 0 &&
    m.parts.every((p) => p.type === "data-approval-request")
  )
}

/** The approval submission this tab sent; the server never stores it. */
function isContinueMessage(m: UIMessage): boolean {
  return m.parts.length > 0 && m.parts.every((p) => p.type === "data-continue")
}

/**
 * Adopt the server transcript wholesale at quiescent boundaries.
 *
 * While a turn streams, useChat owns the transcript; the moment it goes
 * quiescent (`ready`) we adopt the server copy WHOLESALE once it has caught up.
 * Message ids differ between the DB serialization and the live stream, so
 * merging is impossible by design — we replace, never merge.
 *
 * The backend guarantees this is always safe: an approval pause returns 204 on
 * resume, DB history already includes the paused partial turn, and any
 * continuation stream carries only the suffix. A normal turn can still finish
 * before curr_run_id is cleared, though, making the immediate onFinish refetch
 * omit the just-finished rows. Keep the longer live transcript until a later
 * server snapshot catches up. Never adopt while streaming/submitted either.
 */
export function useAdoptServerTranscript({
  status,
  serverMessages,
  liveMessages,
  setMessages,
}: {
  status: ChatStatus
  serverMessages: UIMessage[]
  liveMessages: UIMessage[]
  setMessages: (messages: UIMessage[]) => void
}) {
  const adoptedSignatureRef = useRef<string | null>(null)
  useEffect(() => {
    if (status !== "ready") return
    const serverSignature = transcriptSignature(serverMessages)
    if (adoptedSignatureRef.current === serverSignature) return
    if (transcriptSignature(liveMessages) === serverSignature) {
      adoptedSignatureRef.current = serverSignature
      return
    }
    // A resolved approval drops its card from the server transcript, and the
    // submission is never stored, so neither counts in the finalize-race guard.
    const liveComparableLength = liveMessages.filter(
      (m) => !isApprovalCardMessage(m) && !isContinueMessage(m)
    ).length
    if (serverMessages.length < liveComparableLength) return
    adoptedSignatureRef.current = serverSignature
    setMessages(serverMessages)
  }, [status, serverMessages, liveMessages, setMessages])
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

const EMPTY_BACKENDS: AgentBackendRead[] = []

/** Discover enabled backend providers without coupling the UI to their names. */
export function useAgentBackends(
  workspaceId?: string,
  options?: { enabled?: boolean }
) {
  const { data, isLoading, isSuccess, error, refetch } = useQuery({
    queryKey: ["agent-backends", workspaceId],
    queryFn: () =>
      agentSessionsListAgentBackends({ workspaceId: workspaceId! }),
    enabled: Boolean(workspaceId) && (options?.enabled ?? true),
    staleTime: 60_000,
  })
  return {
    backends: data ?? EMPTY_BACKENDS,
    backendsLoading: isLoading,
    backendsReady: isSuccess,
    backendsError: error,
    refetchBackends: refetch,
  }
}
