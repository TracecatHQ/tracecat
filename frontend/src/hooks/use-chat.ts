import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  type ApiError,
  type ChatCreate,
  type ChatRead,
  type ChatRequest,
  type ChatUpdate,
  type ChatWithMessages,
  chatCreateChat,
  chatGetChat,
  chatListChats,
  chatStartChatTurn,
  chatUpdateChat,
} from "@/client"
import { getBaseUrl } from "@/lib/api"
import { isModelMessage, type ModelMessage } from "@/lib/chat"

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
  entityType?: string
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

// Combined hook for chat functionality with streaming
export function useChat({
  chatId,
  workspaceId,
}: {
  chatId?: string
  workspaceId: string
}) {
  const [messages, setMessages] = useState<ModelMessage[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [isThinking, setIsThinking] = useState(false)
  const [isResponding, setIsResponding] = useState(false)
  const [eventSource, setEventSource] = useState<EventSource | null>(null)

  // Start chat turn mutation
  const mutation = useMutation({
    mutationFn: async (request: ChatRequest) => {
      if (!chatId) {
        throw new Error("No chat ID available")
      }

      const response = await chatStartChatTurn({
        chatId,
        workspaceId,
        requestBody: request,
      })

      return response
    },
    onSuccess: () => {
      setIsResponding(true)
    },
    onError: () => {
      setIsResponding(false)
    },
  })

  // Stream connection effect
  useEffect(() => {
    if (!chatId || !workspaceId) return

    // Clear previous stream data when conversation changes
    setMessages([])

    // Close existing connection
    if (eventSource) {
      eventSource.close()
    }

    // Build the stream URL with workspace_id query parameter
    const url = new URL(`/api/chat/${chatId}/stream`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)

    const newEventSource = new EventSource(url, {
      withCredentials: true,
    })

    newEventSource.onopen = () => {
      setIsConnected(true)
    }

    newEventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data)

        // Validate that the data is a model message using the type guard
        if (!isModelMessage(data)) {
          console.warn("Received invalid message format:", data)
          return
        }

        setMessages((prev) => {
          return [...prev, data]
        })
      } catch (error) {
        console.error("Failed to parse stream data:", error)
      }
    }

    newEventSource.addEventListener("connected", () => {
      setIsConnected(true)
    })

    newEventSource.addEventListener("end", async () => {
      setIsConnected(false)
      setIsThinking(false)
      await new Promise((resolve) => setTimeout(resolve, 1000))
      setIsResponding(false)
    })

    newEventSource.addEventListener("error", () => {
      // Ignore the first error emitted immediately after connect
      if (!isConnected) return
      console.error("Chat stream error")
      setIsConnected(false)
      setIsResponding(false)
    })

    newEventSource.onerror = () => {
      setIsConnected(false)
      setIsResponding(false)
    }

    setEventSource(newEventSource)

    return () => {
      newEventSource.close()
      setIsConnected(false)
      setIsResponding(false)
    }
  }, [chatId, workspaceId])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [])

  // Combine historical messages with real-time stream messages

  return {
    messages,
    sendMessage: mutation.mutateAsync,
    isResponding,
    isConnected,
    isThinking,
  }
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
  } = useQuery<ChatWithMessages, ApiError>({
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
    ChatRead,
    ApiError,
    { chatId: string; update: ChatUpdate }
  >({
    mutationFn: ({ chatId, update }) =>
      chatUpdateChat({
        chatId,
        workspaceId,
        requestBody: update,
      }),
    onSuccess: (data, variables) => {
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
