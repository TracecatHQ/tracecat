import * as aiSdk from "@ai-sdk/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { DefaultChatTransport, type UIMessage } from "ai"
import { useMemo } from "react"
import {
  type ApiError,
  type ChatCreate,
  type ChatEntity,
  type ChatRead,
  type ChatReadMinimal,
  type ChatReadVercel,
  type ChatUpdate,
  chatCreateChat,
  chatGetChat,
  chatGetChatVercel,
  chatListChats,
  chatUpdateChat,
  type VercelChatRequest,
} from "@/client"
import { getBaseUrl } from "@/lib/api"
import type { ModelInfo } from "@/lib/chat"

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
export function useListChats({
  workspaceId,
  entityType,
  entityId,
  limit = 50,
}: {
  workspaceId: string
  entityType?: ChatEntity
  entityId?: string
  limit?: number
}) {
  const {
    data: chats,
    isLoading: chatsLoading,
    error: chatsError,
  } = useQuery<ChatRead[]>({
    queryKey: ["chats", workspaceId, entityType, entityId, limit],
    queryFn: () =>
      chatListChats({
        workspaceId,
        entityType: entityType || null,
        entityId: entityId || null,
        limit,
      }),
  })

  return { chats, chatsLoading, chatsError }
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

  // Build the Vercel streaming endpoint URL
  const apiEndpoint = useMemo(() => {
    if (!chatId) return ""
    const url = new URL(`/api/chat/${chatId}/vercel`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)
    return url.toString()
  }, [chatId, workspaceId])

  // Use Vercel's useChat hook for streaming
  return aiSdk.useChat({
    id: chatId,
    messages,
    transport: new DefaultChatTransport({
      api: apiEndpoint,
      credentials: "include",
      prepareSendMessagesRequest: ({ messages }) => {
        // Send only the last message
        const reqBody: VercelChatRequest = {
          format: "vercel",
          model: modelInfo?.name,
          model_provider: modelInfo?.provider,
          message: messages[messages.length - 1],
        }
        return {
          body: reqBody,
        }
      },
    }),
    onError: (error) => {
      console.error("Error in Vercel chat:", error)
    },
    onFinish: () => {
      queryClient.invalidateQueries({
        queryKey: ["chat", chatId, workspaceId, "vercel"],
      })
      queryClient.invalidateQueries({ queryKey: ["chats", workspaceId] })
    },
  })
}
