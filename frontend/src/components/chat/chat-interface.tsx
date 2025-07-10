"use client"

import { useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import { Bot, ChevronDown, History, MessageSquare, Plus } from "lucide-react"
import { motion } from "motion/react"
import Image from "next/image"
import TracecatIcon from "public/icon.png"
import { useEffect, useRef, useState } from "react"
import type { ChatRead } from "@/client"
import { ModelMessagePart } from "@/components/builder/events/events-selected-action"
import { ChatInput } from "@/components/chat/chat-input"
import { Dots } from "@/components/loading/dots"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useChat, useCreateChat, useListChats } from "@/hooks/use-chat"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

interface ChatInterfaceProps {
  chatId?: string
  entityType: "case" | string
  entityId: string
  onChatSelect?: (chatId: string) => void
}

export function ChatInterface({
  chatId,
  entityType,
  entityId,
  onChatSelect,
}: ChatInterfaceProps) {
  const { workspaceId } = useWorkspace()
  const queryClient = useQueryClient()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    chatId
  )

  // Grab the chats for this entity
  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId: workspaceId,
    entityType: entityType as "case",
    entityId,
  })

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const { sendMessage, isResponding, messages, isConnected, isThinking } =
    useChat({ chatId: selectedChatId, workspaceId })

  // Set the first chat as selected when chats are loaded and no chat is selected
  useEffect(() => {
    if (chats && chats.length > 0 && !selectedChatId) {
      const firstChatId = chats[0].id
      setSelectedChatId(firstChatId)
      onChatSelect?.(firstChatId)
    }
  }, [chats, selectedChatId, onChatSelect])

  // Update selected chat when chatId prop changes
  useEffect(() => {
    if (chatId && chatId !== selectedChatId) {
      setSelectedChatId(chatId)
    }
  }, [chatId, selectedChatId])

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // Real-time invalidation when the agent updates a case
  // TODO: Make this generic and injectable from the parent component
  useEffect(() => {
    if (entityType !== "case" || messages.length === 0) {
      return
    }

    // We have at least 1 message
    const lastMsg = messages[messages.length - 1]

    if (
      lastMsg.kind === "response" &&
      lastMsg.parts.some(
        (p) => "tool_name" in p && p.tool_name === "core__cases__update_case"
      )
    ) {
      console.log("Invalidating case queries")
      // Force-refetch the case & related queries so the UI updates instantly
      queryClient.invalidateQueries({ queryKey: ["case", entityId] })
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["case-events", entityId, workspaceId],
      })
    }
  }, [messages, entityType, entityId, workspaceId, queryClient])

  const handleCreateChat = async () => {
    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: entityType as "case",
        entity_id: entityId,
      })
      setSelectedChatId(newChat.id)
      onChatSelect?.(newChat.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    }
  }

  const handleSelectChat = (chatId: string) => {
    setSelectedChatId(chatId)
    onChatSelect?.(chatId)
  }

  const handleSendMessage = async (message: string) => {
    if (!selectedChatId) return

    try {
      await sendMessage({
        message,
        // TODO: Make this dynamic
        actions: [
          "core.cases.get_case",
          "core.cases.list_cases",
          "core.cases.update_case",
          "tools.slack.post_message",
          "tools.virustotal.lookup_domain",
          "tools.tavily.web_search",
        ],
        model_provider: "openai",
        instructions: `You are a helpful AI assistant helping with ${entityType} management.
        The current ${entityType} ID is ${entityId}. Be concise but thorough in your responses.`,
        context: {
          chatId: selectedChatId,
          workspaceId,
          entityType,
          entityId,
        },
      })
    } catch (error) {
      console.error("Failed to send message:", error)
      // TODO: Add error handling state
    }
  }

  // Show empty state if no chats exist
  if (chats && chats.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {/* Chat Header */}
        <div className="bg-background px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <MessageSquare className="h-4 w-4 text-muted-foreground" />
              <h3 className="text-sm font-semibold">Chat</h3>
            </div>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleCreateChat}
              disabled={createChatPending}
            >
              <Plus className="size-3 mr-1" />
              New chat
            </Button>
          </div>
        </div>

        {/* Empty State */}
        <div className="flex h-full items-center justify-center p-8">
          <div className="text-center max-w-sm">
            <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
            <h4 className="text-sm font-medium text-gray-900 mb-1">
              No chat sessions
            </h4>
            <p className="text-xs text-gray-500 mb-4">
              Create a chat session to start conversing with the AI assistant
              about this {entityType}.
            </p>
            <Button onClick={handleCreateChat} disabled={createChatPending}>
              <Plus className="h-3 w-3 mr-1" />
              Create chat session
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* Chat Header */}
      <div className="bg-background px-4 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold">Chat</h3>
            {isConnected && (
              <div
                className="h-2 w-2 rounded-full bg-green-500"
                title="Connected"
              />
            )}
          </div>

          <div className="flex items-center gap-1">
            {/* New Chat Button */}
            <Button
              size="sm"
              variant="ghost"
              onClick={handleCreateChat}
              disabled={createChatPending}
            >
              <Plus className="size-3 mr-1" />
              New chat
            </Button>

            {/* Past Chats Dropdown */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost">
                  <History className="size-3 mr-1" />
                  Past chats
                  <ChevronDown className="size-3 ml-1" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel>Chat History</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {chatsLoading ? (
                  <div className="p-2">
                    <div className="space-y-2">
                      <div className="h-8 bg-muted animate-pulse rounded" />
                      <div className="h-8 bg-muted animate-pulse rounded" />
                    </div>
                  </div>
                ) : chatsError ? (
                  <DropdownMenuItem disabled>
                    <span className="text-red-600">Failed to load chats</span>
                  </DropdownMenuItem>
                ) : (
                  <ScrollArea className="max-h-64">
                    {chats?.map((chat: ChatRead) => (
                      <DropdownMenuItem
                        key={chat.id}
                        onClick={() => handleSelectChat(chat.id)}
                        className={cn(
                          "flex items-center justify-between cursor-pointer",
                          selectedChatId === chat.id && "bg-accent"
                        )}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">
                            {chat.title}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {formatDistanceToNow(new Date(chat.created_at), {
                              addSuffix: true,
                            })}
                          </div>
                        </div>
                        {selectedChatId === chat.id && (
                          <Badge variant="secondary" className="text-xs ml-2">
                            Active
                          </Badge>
                        )}
                      </DropdownMenuItem>
                    ))}
                  </ScrollArea>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>

      {/* Messages Area */}
      <div className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll p-4 relative no-scrollbar">
        {messages.length === 0 && <NoMessages />}
        {messages.map((message, index) => (
          <ModelMessagePart key={index} part={message} />
        ))}
        {isThinking && (
          <div className="flex gap-3 mb-4">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-muted flex items-center justify-center">
              <Bot className="w-4 h-4 text-muted-foreground" />
            </div>
            <div className="flex-1 max-w-xs sm:max-w-md md:max-w-lg">
              <div className="px-4 py-2 rounded-lg bg-muted">
                <div className="flex items-center gap-2">
                  <div className="flex space-x-1">
                    <div
                      className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce"
                      style={{ animationDelay: "0ms" }}
                    />
                    <div
                      className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce"
                      style={{ animationDelay: "150ms" }}
                    />
                    <div
                      className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce"
                      style={{ animationDelay: "300ms" }}
                    />
                  </div>
                  <span className="text-sm text-muted-foreground">
                    AI is thinking...
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
        {isResponding && (
          <motion.div
            className="flex gap-3 items-center"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
          >
            <Image src={TracecatIcon} alt="Tracecat" className="size-4" />
            <Dots />
          </motion.div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Chat Input */}
      <ChatInput
        onSendMessage={handleSendMessage}
        disabled={isResponding || !selectedChatId}
        placeholder={`Ask about this ${entityType}...`}
      />
    </div>
  )
}

function NoMessages() {
  return (
    <div className="flex h-full items-center justify-center text-center">
      <div className="max-w-sm">
        <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
        <h4 className="text-sm font-medium text-gray-900 mb-1">
          Start a conversation
        </h4>
        <p className="text-xs text-gray-500">
          Ask me anything about this case or get help with investigation tasks.
        </p>
      </div>
    </div>
  )
}
