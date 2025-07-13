"use client"

import { formatDistanceToNow } from "date-fns"
import {
  ChevronDown,
  History,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Save,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { ChatRead } from "@/client"
import { ChatInput } from "@/components/chat/chat-input"
import { Messages } from "@/components/chat/messages"
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
import { useCreatePrompt } from "@/hooks/use-prompt"
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

  // Create prompt mutation
  const { createPrompt, createPromptPending } = useCreatePrompt(workspaceId)

  const { sendMessage, isResponding, messages, isConnected } = useChat({
    chatId: selectedChatId,
    workspaceId,
  })

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

  const handleSaveAsPrompt = async () => {
    if (!selectedChatId) {
      console.warn("No chat selected")
      return
    }

    // Check if chat has messages
    if (!messages || messages.length === 0) {
      console.warn("Cannot save empty chat as prompt")
      return
    }

    try {
      const prompt = await createPrompt({
        chat_id: selectedChatId,
      })

      console.log(`Chat saved as prompt: "${prompt.title}"`)
    } catch (error) {
      console.error("Failed to save chat as prompt:", error)
    }
  }

  const handleSendMessage = async (message: string) => {
    if (!selectedChatId) return

    try {
      await sendMessage({
        message,
        // TODO: Make this dynamic
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

            {/* Chat Actions Dropdown */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  className="size-6 p-0"
                  disabled={!selectedChatId}
                >
                  <MoreHorizontal className="h-3 w-3" />
                  <span className="sr-only">More options</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem
                  onClick={handleSaveAsPrompt}
                  disabled={createPromptPending || !messages?.length}
                  className="text-xs"
                >
                  <Save className="mr-2 h-3 w-3" />
                  Save as prompt
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>

      {/* Messages Area */}
      <Messages
        messages={messages}
        isResponding={isResponding}
        entityType={entityType}
        entityId={entityId}
        workspaceId={workspaceId}
      />

      {/* Chat Input */}
      {selectedChatId && (
        <ChatInput
          onSendMessage={handleSendMessage}
          disabled={isResponding || !selectedChatId}
          placeholder={`Ask about this ${entityType}...`}
          chatId={selectedChatId}
        />
      )}
    </div>
  )
}
