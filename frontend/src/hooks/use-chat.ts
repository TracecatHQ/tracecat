import * as aiSdk from "@ai-sdk/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { DefaultChatTransport, type UIMessage } from "ai"
import { useEffect, useMemo, useRef, useState } from "react"
import {
  type ApiError,
  type BasicChatRequest,
  type ChatCreate,
  type ChatEntity,
  type ChatRead,
  type ChatReadMinimal,
  type ChatReadVercel,
  type ChatResponse,
  type ChatUpdate,
  chatCreateChat,
  chatGetChat,
  chatGetChatVercel,
  chatListChats,
  chatStartChatTurn,
  chatUpdateChat,
  type VercelChatRequest,
} from "@/client"
import { getBaseUrl } from "@/lib/api"
import type { ModelInfo } from "@/lib/chat"
import { isModelMessage, isStreamEvent, type ModelMessage } from "@/lib/chat"

const serializeMessageForComparison = (message: ModelMessage) => {
  const normalized = {
    kind: message.kind,
    parts: message.parts.map((part) => {
      if (typeof part !== "object" || part === null) {
        return part
      }

      const { part_kind } = part as { part_kind?: string }

      switch (part_kind) {
        case "text":
          return {
            part_kind: "text",
            content:
              "content" in part
                ? ((part as { content?: string }).content ?? "")
                : "",
          }
        case "user-prompt":
          return {
            part_kind: "user-prompt",
            content:
              "content" in part
                ? ((part as { content?: string | string[] }).content ?? "")
                : "",
          }
        default:
          return part
      }
    }),
  }

  return JSON.stringify(normalized)
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const deepMergeRecords = (
  target: Record<string, unknown>,
  source: Record<string, unknown>
) => {
  const result: Record<string, unknown> = { ...target }
  for (const [key, value] of Object.entries(source)) {
    if (isRecord(value) && isRecord(result[key])) {
      result[key] = deepMergeRecords(
        result[key] as Record<string, unknown>,
        value
      )
      continue
    }
    result[key] = value
  }
  return result
}

const reconcileToolCallDelta = (current: string, incoming: unknown) => {
  if (incoming === undefined || incoming === null) {
    return current
  }

  if (typeof incoming === "string") {
    return current + incoming
  }

  if (isRecord(incoming)) {
    let parsedCurrent: Record<string, unknown> | null = null
    if (current.trim().length > 0) {
      try {
        const parsed = JSON.parse(current)
        if (isRecord(parsed)) {
          parsedCurrent = parsed
        }
      } catch (error) {
        console.warn(
          "Failed to parse existing tool call buffer as JSON, replacing with latest args",
          error
        )
      }
    }

    const merged = deepMergeRecords(parsedCurrent ?? {}, incoming)
    return JSON.stringify(merged)
  }

  try {
    return current + JSON.stringify(incoming)
  } catch (error) {
    console.warn(
      "Failed to stringify incoming tool call delta, falling back to string conversion",
      error
    )
    return current + String(incoming)
  }
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

// Combined hook for chat functionality with streaming
export function useChat({
  chatId,
  workspaceId,
}: {
  chatId?: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()
  const [streamMessages, setStreamMessages] = useState<ModelMessage[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [isResponding, setIsResponding] = useState(false)
  const [eventSource, setEventSource] = useState<EventSource | null>(null)
  const [streamingText, setStreamingText] = useState<string>("")
  const textRef = useRef<string>("")
  const toolCallRef = useRef<string>("")
  const rafHandle = useRef<number | null>(null)
  const historyReadyRef = useRef(false)

  useEffect(() => {
    setStreamMessages([])
    textRef.current = ""
    toolCallRef.current = ""
    setStreamingText("")
    historyReadyRef.current = false
  }, [chatId])

  const { data: chatHistory, isSuccess: historySuccess } = useQuery<
    ChatRead,
    ApiError
  >({
    queryKey: ["chat", chatId, workspaceId],
    queryFn: () => {
      if (!chatId) {
        throw new Error("No chat ID available")
      }
      return chatGetChat({ chatId, workspaceId })
    },
    enabled: !!chatId && !!workspaceId,
  })

  const historyMessages = useMemo(() => {
    if (!chatHistory?.messages) {
      return []
    }
    return chatHistory.messages.map((entry) => entry.message)
  }, [chatHistory])

  useEffect(() => {
    if (!historySuccess || historyMessages.length === 0) {
      return
    }

    setStreamMessages((current) => {
      if (current.length === 0) {
        return current
      }

      const persisted = new Set(
        historyMessages.map((message) => serializeMessageForComparison(message))
      )

      return current.filter(
        (message) => !persisted.has(serializeMessageForComparison(message))
      )
    })
  }, [historySuccess, historyMessages])

  const messages = useMemo(() => {
    if (streamMessages.length === 0) {
      return historyMessages
    }

    return [...historyMessages, ...streamMessages]
  }, [historyMessages, streamMessages])

  // Start chat turn mutation
  const { mutateAsync: sendMessage } = useMutation<
    ChatResponse,
    ApiError,
    BasicChatRequest
  >({
    mutationFn: async (request: BasicChatRequest) => {
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
    onMutate: () => {
      textRef.current = ""
      toolCallRef.current = ""
      setStreamingText("")
      // We don't have to optimistically set the user message here because it will be streamed immediately
      setIsResponding(true)
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
    if (!chatId || !workspaceId || !historySuccess) {
      return
    }

    if (historyReadyRef.current) {
      return
    }
    historyReadyRef.current = true

    setStreamMessages([])
    textRef.current = ""
    toolCallRef.current = ""
    setStreamingText("")

    // Build the stream URL with workspace_id query parameter
    const url = new URL(`/api/chat/${chatId}/stream`, getBaseUrl())
    url.searchParams.set("workspace_id", workspaceId)

    const newEventSource = new EventSource(url, {
      withCredentials: true,
    })

    newEventSource.onopen = () => {
      setIsConnected(true)
    }

    newEventSource.addEventListener(
      "message",
      (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data)

          // Validate that the data is a model message using the type guard
          if (!isModelMessage(data)) {
            console.warn(
              "Received invalid message format for message event:",
              data
            )
            return
          }

          setStreamMessages((prev) => {
            // perf - can we use the redis stream id instead?
            const next = [...prev]
            const incomingKey = serializeMessageForComparison(data)
            const existingIndex = next.findIndex(
              (message) =>
                serializeMessageForComparison(message) === incomingKey
            )

            if (existingIndex !== -1) {
              next[existingIndex] = data
              return next
            }

            next.push(data)
            return next
          })

          // Clear draft when we receive a full assistant message
          if (data.parts.some((part) => part.part_kind === "text")) {
            textRef.current = ""
            toolCallRef.current = ""
            setStreamingText("")
          }
        } catch (error) {
          console.error("Failed to parse stream data:", error)
        }
      }
    )

    newEventSource.addEventListener("delta", (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data)
        if (!isStreamEvent(payload)) {
          console.warn(
            "Received invalid delta message format for delta event:",
            payload
          )
          return
        }
        console.log("Received delta event:", payload)
        switch (payload.event_kind) {
          case "part_start": {
            const part = payload.part
            switch (part.part_kind) {
              case "text":
                textRef.current += part.content || ""
                break
              case "tool-call":
                console.log("Handling tool call part start:", part)
                toolCallRef.current = reconcileToolCallDelta(
                  toolCallRef.current,
                  part.args
                )
                break
              default:
                console.log("Skip part start:", part)
                break
            }
            break
          }
          case "part_delta": {
            const delta = payload.delta
            switch (delta.part_delta_kind) {
              case "text":
                textRef.current += delta.content_delta || ""
                break
              case "tool_call":
                console.log("Handling tool call part delta:", delta)
                toolCallRef.current = reconcileToolCallDelta(
                  toolCallRef.current,
                  delta.args_delta
                )
                break
              default:
                console.log("Skip part delta:", delta)
                break
            }
            break
          }
          case "function_tool_call":
            console.log("Handling function tool call:", payload.part)
            break
          case "function_tool_result":
            console.log("Handling function tool result:", payload.result)
            switch (payload.result.part_kind) {
              case "tool-return":
                // do we need to refetch the chat history here?
                toolCallRef.current = reconcileToolCallDelta(
                  toolCallRef.current,
                  payload.result.content
                )
                break
              case "retry-prompt":
                toolCallRef.current = reconcileToolCallDelta(
                  toolCallRef.current,
                  payload.result.content
                )
                break
              default:
                console.log("Handling function tool result:", payload.result)
                break
            }
            break
          case "builtin_tool_call":
            console.log("Handling builtin tool call:", payload.part)
            break
          case "final_result":
            console.log("Handling final result:", payload)
            // This eagerly sets the response message when the final result is received
            // so that there isn't a delay before the response message is displayed
            // when we refetch the chat history
            if (textRef.current.trim().length > 0) {
              const responseMessage: ModelMessage = {
                kind: "response",
                parts: [
                  {
                    part_kind: "text",
                    content: textRef.current,
                  },
                ],
              }
              console.log("Setting response message:", responseMessage)
              setStreamMessages((prev) => [...prev, responseMessage])
            }
            textRef.current = ""
            toolCallRef.current = ""
            setStreamingText("")
            if (chatId) {
              queryClient.invalidateQueries({
                queryKey: ["chat", chatId, workspaceId],
              })
            }
            break

          default:
            console.warn(
              "Received invalid delta message format for delta event:",
              payload
            )
            break
        }

        // Use requestAnimationFrame to batch UI updates
        if (rafHandle.current === null) {
          rafHandle.current = requestAnimationFrame(() => {
            setStreamingText(textRef.current)
            rafHandle.current = null
          })
        }
      } catch (error) {
        console.error("Failed to parse delta data:", error)
      }
    })

    newEventSource.addEventListener("connected", () => {
      setIsConnected(true)
    })

    newEventSource.addEventListener("end", () => {
      setIsConnected(false)
      setIsResponding(false)
      if (chatId) {
        queryClient.invalidateQueries({
          queryKey: ["chat", chatId, workspaceId],
        })
      }
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
      setEventSource(null)
      setIsConnected(false)
      setIsResponding(false)
      // Cancel any pending requestAnimationFrame
      if (rafHandle.current !== null) {
        cancelAnimationFrame(rafHandle.current)
        rafHandle.current = null
      }
    }
  }, [chatId, workspaceId, historySuccess])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSource) {
        eventSource.close()
      }
      // Cancel any pending requestAnimationFrame on unmount
      if (rafHandle.current !== null) {
        cancelAnimationFrame(rafHandle.current)
        rafHandle.current = null
      }
    }
  }, [])

  // Combine historical messages with real-time stream messages

  return {
    messages,
    sendMessage,
    isResponding,
    isConnected,
    streamingText: streamingText,
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
        // TODO: Make this dynamic
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
