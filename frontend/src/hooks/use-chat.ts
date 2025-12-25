import * as aiSdk from "@ai-sdk/react"
import {
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { DefaultChatTransport, type UIMessage } from "ai"
import { useMemo, useState } from "react"
import {
  type ApiError,
  type ChatCreate,
  type ChatEntity,
  type ChatRead,
  type ChatReadMinimal,
  type ChatReadVercel,
  type ChatUpdate,
  type ContinueRunRequest,
  chatCreateChat,
  chatDeleteChat,
  chatGetChat,
  chatGetChatVercel,
  chatListChats,
  chatUpdateChat,
  type VercelChatRequest,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getBaseUrl } from "@/lib/api"
import type { ModelInfo } from "@/lib/chat"

const DEFAULT_CHAT_ERROR_MESSAGE =
  "The assistant couldn't complete that request. Please try again."

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
    ChatRead,
    ApiError,
    ChatCreate
  >({
    mutationFn: (request: ChatCreate) =>
      chatCreateChat({ requestBody: request, workspaceId }),
    onSuccess: () => {
      // Invalidate and refetch chat lists
      // TODO: Add entityType/entityId here
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
    entityType?: ChatEntity
    entityId?: string
    limit?: number
  },
  options?: { enabled?: boolean }
): {
  chats: ChatRead[] | undefined
  chatsLoading: boolean
  chatsError: Error | null
  refetchChats: UseQueryResult<ChatRead[], Error>["refetch"]
} {
  const {
    data: chats,
    isLoading: chatsLoading,
    error: chatsError,
    refetch,
  } = useQuery<ChatRead[]>({
    queryKey: ["chats", workspaceId, entityType, entityId, limit],
    queryFn: () =>
      chatListChats({
        workspaceId,
        entityType: entityType || null,
        entityId: entityId || null,
        limit,
      }),
    enabled: options?.enabled ?? true,
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
  } = useQuery<ChatRead, ApiError>({
    queryKey: ["chat", chatId, workspaceId],
    queryFn: () => chatGetChat({ chatId, workspaceId }),
    enabled: !!chatId && !!workspaceId,
  })

  return { chat, isLoading, error }
}

// Hook for updating a chat
export function useUpdateChat(workspaceId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation<
    ChatReadMinimal,
    ApiError,
    { chatId: string; update: ChatUpdate }
  >({
    mutationFn: ({ chatId, update }) =>
      chatUpdateChat({
        chatId,
        workspaceId,
        requestBody: update,
      }),
    onSuccess: (_, variables) => {
      // Invalidate and refetch chat data
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
      chatDeleteChat({
        chatId,
        workspaceId,
      }),
    onSuccess: (_, variables) => {
      // Invalidate and refetch chat data
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
  } = useQuery<ChatReadVercel, ApiError>({
    queryKey: ["chat", chatId, workspaceId, "vercel"],
    queryFn: async () => {
      if (!chatId) {
        throw new Error("No chat ID available")
      }
      return await chatGetChatVercel({ chatId, workspaceId })
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
    const url = new URL(`/api/chat/${chatId}/vercel`, getBaseUrl())
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
          }
          return { body }
        }

        // Default: start a vercel chat turn with the last UI message
        const body: VercelChatRequest = {
          kind: "vercel",
          model: modelInfo?.name,
          model_provider: modelInfo?.provider,
          message: last,
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
    clearError: () => setLastError(null),
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
  decisions: ContinueRunRequest["decisions"]
): UIMessage {
  return {
    id: `continue-${Date.now()}`,
    role: "user",
    parts: [
      {
        type: "data-continue",
        data: { format: "continue", decisions },
      } as UIMessage["parts"][number],
    ],
  }
}
