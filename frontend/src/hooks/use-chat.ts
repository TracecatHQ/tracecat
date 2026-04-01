import * as aiSdk from "@ai-sdk/react"
import {
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { DefaultChatTransport, type UIMessage } from "ai"
import { useCallback, useMemo, useState } from "react"
import {
  type AgentSessionCreate,
  type AgentSessionEntity,
  type AgentSessionRead,
  type AgentSessionsGetSessionResponse,
  type AgentSessionsGetSessionVercelResponse,
  type AgentSessionsListSessionsResponse,
  type AgentSessionUpdate,
  type ApiError,
  agentSessionsCreateSession,
  agentSessionsDeleteSession,
  agentSessionsGetSession,
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

// Combined hook for chat functionality with Vercel AI SDK streaming
export function useVercelChat({
  chatId,
  workspaceId,
  messages,
  modelInfo,
}: {
  chatId?: string
  workspaceId: string
  messages: UIMessage[]
  modelInfo: ModelInfo
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

  // Use Vercel's useChat hook for streaming
  const chat = aiSdk.useChat({
    id: chatId,
    messages,
    transport: new DefaultChatTransport({
      api: apiEndpoint,
      credentials: "include",
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
      queryClient.invalidateQueries({
        queryKey: ["chat", chatId, workspaceId, "vercel"],
      })
      queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
    },
  })

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
