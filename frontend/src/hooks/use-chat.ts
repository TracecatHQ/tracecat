import * as aiSdk from "@ai-sdk/react"
import {
  type QueryClient,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  type ChatOnDataCallback,
  type ChatStatus,
  DefaultChatTransport,
  type UIMessage,
} from "ai"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  type AgentSessionCreate,
  type AgentSessionEntity,
  type AgentSessionRead,
  type AgentSessionsCancelSessionResponse,
  type AgentSessionsGetSessionResponse,
  type AgentSessionsGetSessionVercelResponse,
  type AgentSessionsListSessionsResponse,
  type AgentSessionsRemoveSessionArtifactData,
  type AgentSessionsRemoveSessionArtifactResponse,
  type AgentSessionUpdate,
  type ApiError,
  agentSessionsCancelSession,
  agentSessionsCreateSession,
  agentSessionsDeleteSession,
  agentSessionsGetSession,
  agentSessionsGetSessionVercel,
  agentSessionsListSessions,
  agentSessionsRemoveSessionArtifact,
  agentSessionsUpdateSession,
  type ContinueRunRequest,
  type VercelChatRequest,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getBaseUrl } from "@/lib/api"
import { type ModelInfo, toServerUIMessage } from "@/lib/chat"

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
    // A remount must never render a stale cache snapshot as the final
    // transcript: always refetch so the pane adopts the current server copy
    // (e.g. after an approval was resolved from another surface).
    refetchOnMount: "always",
  })
  return { chat, chatLoading, chatError }
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
  resume = true,
}: {
  chatId?: string
  workspaceId: string
  messages: UIMessage[]
  modelInfo: ModelInfo
  onData?: ChatOnDataCallback<UIMessage>
  /**
   * Reconnect to the live event stream on mount. Defaults to true. Set to
   * false for terminal sessions whose history is already seeded from the DB:
   * resuming there replays the last persisted turn on top of the seeded
   * messages, duplicating the chat.
   */
  resume?: boolean
}) {
  const queryClient = useQueryClient()
  const [lastError, setLastError] = useState<string | null>(null)

  // Build the Vercel streaming endpoint URL
  const apiEndpoint = useMemo(() => {
    if (!chatId) return ""
    const url = new URL(`/api/agent/sessions/${chatId}/messages`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)
    return url.toString()
  }, [chatId, workspaceId])

  // Use Vercel's useChat hook for streaming. On reconnect the server replays the
  // whole active turn from the start (it hides the active run's DB rows mid-turn,
  // so Redis is the sole source for the live assistant); we therefore do not send
  // a Last-Event-ID cursor.
  const chat = aiSdk.useChat({
    id: chatId,
    resume: !!chatId && resume,
    messages,
    transport: new DefaultChatTransport({
      api: apiEndpoint,
      credentials: "include",
      prepareReconnectToStreamRequest: ({ id }) => {
        const url = new URL(`/api/agent/sessions/${id}/stream`, getBaseUrl())
        url.searchParams.set("workspace_id", workspaceId)
        return {
          api: url.toString(),
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
    onFinish: () => {
      setLastError(null)
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
    onData,
  })

  return {
    ...chat,
    lastError,
    clearError: useCallback(() => setLastError(null), []),
  }
}

/**
 * Identity signature of a transcript: message ids plus the id, type, state, and
 * text of each part. Two transcripts with the same signature carry the same
 * rendered content, so an unchanged signature means there is nothing to adopt.
 */
function transcriptSignature(messages: UIMessage[]): string {
  return JSON.stringify(
    messages.map((m) => [
      m.id,
      m.parts.map((p) => [
        p.type,
        "state" in p ? p.state : undefined,
        "text" in p ? p.text : undefined,
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

/** Delays between a rejected transcript adoption and its bounded refetches. */
export const ADOPT_SERVER_TRANSCRIPT_RETRY_DELAYS_MS = [
  1_000, 3_000, 8_000,
] as const

/** The reason a server transcript should be adopted or retained. */
export type ServerTranscriptAdoptionDecision =
  | "already-current"
  | "adopt"
  | "reject-content"
  | "reject-count"

/** Index of the last user-role message, or -1 when there is none. */
function lastUserMessageIndex(messages: UIMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "user") {
      return index
    }
  }
  return -1
}

/**
 * Concatenate the text parts of the final non-approval assistant message.
 *
 * Only the final turn qualifies: the scan stops at the last user message, so
 * an assistant answer from a previous turn is never used as the coverage
 * probe. `null` means there is no comparable text, so callers must fall back
 * to the count guard instead of comparing tool or data parts that serialize
 * differently between the stream and the database.
 */
export function getFinalLiveAssistantText(
  liveMessages: UIMessage[]
): string | null {
  const turnStart = lastUserMessageIndex(liveMessages) + 1
  for (let index = liveMessages.length - 1; index >= turnStart; index -= 1) {
    const message = liveMessages[index]
    if (message.role !== "assistant" || isApprovalCardMessage(message)) {
      continue
    }

    let hasTextPart = false
    let text = ""
    for (const part of message.parts) {
      if (part.type === "text") {
        hasTextPart = true
        text += part.text
      }
    }
    return hasTextPart ? text : null
  }
  return null
}

/**
 * Whether the server snapshot contains the live final assistant text.
 *
 * Both sides are scoped to the final turn: the probe is the live assistant
 * text after the last live user message, and it must appear in the server
 * text after the last server user message. Comparing whole transcripts would
 * let a final answer that repeats earlier conversation text mask a snapshot
 * that still omits the new turn. Text is concatenated across the server
 * turn's messages so one live assistant bubble can match several
 * database-backed rows. A textless final turn is deliberately treated as
 * covered and left to the count guard.
 */
export function serverTranscriptCoversLiveFinalAssistantText(
  serverMessages: UIMessage[],
  liveMessages: UIMessage[]
): boolean {
  const finalAssistantText = getFinalLiveAssistantText(liveMessages)
  if (finalAssistantText === null) {
    return true
  }

  let serverText = ""
  for (const message of serverMessages.slice(
    lastUserMessageIndex(serverMessages) + 1
  )) {
    for (const part of message.parts) {
      if (part.type === "text") {
        serverText += part.text
      }
    }
  }
  return serverText.includes(finalAssistantText)
}

/**
 * Decide whether a quiescent live transcript can be replaced by the server.
 *
 * Count coverage always applies. Content coverage additionally protects the
 * final streamed assistant text unless a bounded retry episode is exhausted.
 */
export function decideServerTranscriptAdoption({
  serverMessages,
  liveMessages,
  allowContentMismatch = false,
}: {
  serverMessages: UIMessage[]
  liveMessages: UIMessage[]
  allowContentMismatch?: boolean
}): ServerTranscriptAdoptionDecision {
  if (
    transcriptSignature(liveMessages) === transcriptSignature(serverMessages)
  ) {
    return "already-current"
  }

  // A resolved approval drops its card from the server transcript, so exclude
  // approval-card-only messages from the finalize-race length guard.
  const liveComparableLength = liveMessages.filter(
    (message) => !isApprovalCardMessage(message)
  ).length
  if (serverMessages.length < liveComparableLength) {
    return "reject-count"
  }
  if (
    !allowContentMismatch &&
    !serverTranscriptCoversLiveFinalAssistantText(serverMessages, liveMessages)
  ) {
    return "reject-content"
  }
  return "adopt"
}

type TranscriptRetryEpisode = {
  completedAttempts: number
  key: string
  timers: Set<ReturnType<typeof setTimeout>>
}

/**
 * Adopt the server transcript wholesale at quiescent boundaries.
 *
 * While a turn streams, useChat owns the transcript; the moment it goes
 * quiescent (`ready`) we adopt the server copy WHOLESALE once it has caught up.
 * Message ids differ between the DB serialization and the live stream, so
 * merging is impossible by design — we replace, never merge.
 *
 * The approval/continuation contract makes wholesale replacement safe once the
 * DB snapshot is current: an approval pause returns 204 on resume, DB history
 * already includes the paused partial turn, and any continuation stream carries
 * only the suffix. A normal turn can still finish before curr_run_id is cleared,
 * though, making the immediate onFinish refetch omit the just-finished rows.
 * Keep the live transcript until a later server snapshot covers its final
 * assistant text and comparable message count.
 * Rejected snapshots receive a bounded refetch series; after the final retry,
 * a snapshot that passes the count guard is adopted even if its text still
 * differs because the server is canonical at rest. Never adopt or refetch from
 * these timers while streaming/submitted.
 */
export function useAdoptServerTranscript({
  chatId,
  workspaceId,
  status,
  serverMessages,
  liveMessages,
  setMessages,
}: {
  chatId?: string
  workspaceId: string
  status: ChatStatus
  serverMessages: UIMessage[]
  liveMessages: UIMessage[]
  setMessages: (messages: UIMessage[]) => void
}) {
  const queryClient = useQueryClient()
  const retryEpisodeRef = useRef<TranscriptRetryEpisode | null>(null)
  const statusRef = useRef(status)
  const [retryRevision, setRetryRevision] = useState(0)
  statusRef.current = status

  const cancelRetryEpisode = useCallback(function cancelRetryEpisode(): void {
    const episode = retryEpisodeRef.current
    if (!episode) return
    for (const timer of episode.timers) {
      clearTimeout(timer)
    }
    retryEpisodeRef.current = null
  }, [])

  const scheduleRetryEpisode = useCallback(
    function scheduleRetryEpisode(key: string): void {
      cancelRetryEpisode()
      if (!chatId) return

      const episode: TranscriptRetryEpisode = {
        completedAttempts: 0,
        key,
        timers: new Set(),
      }
      retryEpisodeRef.current = episode

      for (const [
        attemptIndex,
        delayMs,
      ] of ADOPT_SERVER_TRANSCRIPT_RETRY_DELAYS_MS.entries()) {
        const timer = setTimeout(() => {
          episode.timers.delete(timer)
          if (
            retryEpisodeRef.current !== episode ||
            statusRef.current !== "ready"
          ) {
            return
          }

          void queryClient
            .invalidateQueries({
              queryKey: ["chat", chatId, workspaceId, "vercel"],
            })
            .catch(() => undefined)
            .then(() => {
              if (
                retryEpisodeRef.current !== episode ||
                statusRef.current !== "ready"
              ) {
                return
              }
              episode.completedAttempts = Math.max(
                episode.completedAttempts,
                attemptIndex + 1
              )
              setRetryRevision((revision) => revision + 1)
            })
        }, delayMs)
        episode.timers.add(timer)
      }
    },
    [cancelRetryEpisode, chatId, queryClient, workspaceId]
  )

  useEffect(() => cancelRetryEpisode, [cancelRetryEpisode])

  useEffect(() => {
    if (status !== "ready") {
      cancelRetryEpisode()
      return
    }

    const liveSignature = transcriptSignature(liveMessages)
    const episodeKey = JSON.stringify([chatId, workspaceId, liveSignature])
    const retryEpisode = retryEpisodeRef.current
    const allowContentMismatch =
      retryEpisode?.key === episodeKey &&
      retryEpisode.completedAttempts >=
        ADOPT_SERVER_TRANSCRIPT_RETRY_DELAYS_MS.length
    const decision = decideServerTranscriptAdoption({
      serverMessages,
      liveMessages,
      allowContentMismatch,
    })

    if (decision === "already-current") {
      cancelRetryEpisode()
      return
    }
    if (decision === "reject-content" || decision === "reject-count") {
      if (retryEpisode?.key !== episodeKey) {
        scheduleRetryEpisode(episodeKey)
      }
      return
    }

    cancelRetryEpisode()
    setMessages(serverMessages)
  }, [
    cancelRetryEpisode,
    chatId,
    liveMessages,
    retryRevision,
    scheduleRetryEpisode,
    serverMessages,
    setMessages,
    status,
    workspaceId,
  ])
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
