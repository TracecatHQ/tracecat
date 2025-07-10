import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  type ApiError,
  type ChatCreate,
  type ChatRead,
  type ChatRequest,
  chatCreateChat,
  chatListChats,
  chatStartChatTurn,
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
  const [eventSource, setEventSource] = useState<EventSource | null>(null)

  // // Historical messages query using the new chat API
  // const {
  // 	data: chatData,
  // 	isLoading: messagesLoading,
  // 	error: messagesError,
  // } = useQuery({
  // 	queryKey: ["chat-messages", chatId],
  // 	queryFn: async () => {
  // 		if (!chatId) return null;
  // 		const response = await chatGetChat({ chatId, workspaceId });

  // 		// Convert Redis messages to ChatMessage format
  // 		const messages = (response.messages || []).map(
  // 			(msg: unknown, index: number) => {
  // 				const msgObj = msg as Record<string, unknown>;
  // 				return {
  // 					id: (msgObj.redis_id as string) || index.toString(),
  // 					type: (msgObj.type as string) || "unknown",
  // 					content: (msgObj.content as string) || "",
  // 					tool_name: msgObj.tool_name as string | undefined,
  // 					timestamp: new Date().toISOString(), // Redis doesn't store timestamps
  // 				};
  // 			},
  // 		) as ChatMessage[];

  // 		return messages;
  // 	},
  // 	enabled: !!chatId,
  // });

  // const historicalMessages = chatData || [];

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

        console.log({ data })

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

    newEventSource.addEventListener("end", () => {
      setIsConnected(false)
      setIsThinking(false)
    })

    newEventSource.addEventListener("error", () => {
      // Ignore the first error emitted immediately after connect
      if (!isConnected) return
      console.error("Chat stream error")
      setIsConnected(false)
    })

    newEventSource.onerror = () => {
      setIsConnected(false)
    }

    setEventSource(newEventSource)

    return () => {
      newEventSource.close()
      setIsConnected(false)
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
    // messagesLoading,
    // messagesError,
    sendMessage: mutation.mutateAsync,
    isSending: mutation.isPending,
    sendError: mutation.error,
    isConnected,
    isThinking,
  }
}
