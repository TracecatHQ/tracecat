"use client"

import { formatDistanceToNow } from "date-fns"
import { ChevronDown, ListTodo, MessageSquare, Plus } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type { ChatEntity, ChatRead } from "@/client"
import { RunbookDropdown } from "@/components/cases/runbook-dropdown"
import { ChatInput } from "@/components/chat/chat-input"
import { Messages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useChat, useCreateChat, useListChats } from "@/hooks/use-chat"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useCreateRunbook, useGetRunbook } from "@/hooks/use-runbook"
import { useChatReadiness } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface ChatInterfaceProps {
  chatId?: string
  entityType: ChatEntity
  entityId: string
  onChatSelect?: (chatId: string) => void
}

export function ChatInterface({
  chatId,
  entityType,
  entityId,
  onChatSelect,
}: ChatInterfaceProps) {
  const workspaceId = useWorkspaceId()
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    chatId
  )

  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId: workspaceId,
    entityType,
    entityId,
  })

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  // Create prompt mutation
  const { createRunbook, createRunbookPending } = useCreateRunbook(workspaceId)
  const { isFeatureEnabled } = useFeatureFlag()
  const runbooksEnabled = isFeatureEnabled("runbooks")

  // Fetch runbook data if entityType is "runbook"
  const { data: runbookData } = useGetRunbook({
    runbookId: entityType === "runbook" ? entityId : "",
    workspaceId,
    enabled: runbooksEnabled && entityType === "runbook",
  })

  const { sendMessage, isResponding, messages } = useChat({
    chatId: selectedChatId,
    workspaceId,
  })

  /* chat readiness -------------------- */
  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReason,
    provider,
  } = useChatReadiness()

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
        entity_type: entityType,
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
    if (!runbooksEnabled) {
      return
    }
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
      const runbook = await createRunbook({
        chat_id: selectedChatId,
      })

      console.log(`Chat saved as prompt: "${runbook.title}"`)
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
        instructions:
          entityType === "runbook"
            ? `You are a helpful AI assistant helping with runbook editing.
        The current runbook ID is: ${entityId}
        ${runbookData ? `The runbook title is: "${runbookData.title}"` : ""}
        You can use the update_prompt tool to edit the runbook's title or instructions.
        Be concise but thorough in your responses.`
            : `You are a helpful AI assistant helping with ${entityType} management.
        The current ${entityType} ID is: ${entityId}
        If you need to use the ${entityType} ID in a tool call you should use the above ID.
        Be concise but thorough in your responses.`,
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
      <div className="px-4 py-2">
        <div className="flex items-center justify-between">
          {/* Unified New-chat / History dropdown */}
          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" className="px-0">
                  Conversations
                  <ChevronDown className="size-3 ml-1" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64">
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
                      </DropdownMenuItem>
                    ))}
                  </ScrollArea>
                )}
              </DropdownMenuContent>
            </DropdownMenu>

            {/* (left-side plus removed) */}
          </div>

          {/* Right-side controls: new chat + actions */}
          <div className="flex items-center gap-1">
            {/* New chat icon button with tooltip */}
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="size-6 p-0"
                    onClick={handleCreateChat}
                    disabled={createChatPending}
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">New chat</TooltipContent>
              </Tooltip>
            </TooltipProvider>

            {/* Generate runbook icon button with tooltip - only show for non-runbook entities */}
            {runbooksEnabled && entityType !== "runbook" && (
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="size-6 p-0"
                      onClick={handleSaveAsPrompt}
                      disabled={createRunbookPending || !messages?.length}
                    >
                      <ListTodo className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    Generate runbook
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            {runbooksEnabled && entityType === "case" && (
              <RunbookDropdown
                workspaceId={workspaceId}
                entityType={entityType}
                entityId={entityId}
              />
            )}
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
        <>
          {chatReady ? (
            <ChatInput
              onSendMessage={handleSendMessage}
              disabled={isResponding}
              placeholder={
                entityType === "runbook"
                  ? "Ask about or edit this runbook..."
                  : `Ask about this ${entityType}...`
              }
              chatId={selectedChatId}
              entityType={entityType}
            />
          ) : chatReadyLoading ? (
            <CenteredSpinner />
          ) : (
            /* Disabled notice with quick-fix button */
            <Link
              href="/organization/settings/agent"
              className="pb-2 block border-t border-border bg-gradient-to-r from-muted/30 to-muted/50 backdrop-blur-sm hover:from-muted/40 hover:to-muted/60 transition-all duration-200"
            >
              <div className="p-4">
                <div className="flex items-center gap-3">
                  <div className="flex-1">
                    <h4 className="text-sm font-medium text-foreground mb-1">
                      {chatReason === "no_model" && "No default model"}
                      {chatReason === "no_credentials" && "Missing credentials"}
                    </h4>
                    <p className="text-xs text-muted-foreground">
                      {chatReason === "no_model" &&
                        "Select a default model in agent settings to enable chat."}
                      {chatReason === "no_credentials" &&
                        `Configure ${provider} credentials in agent settings to enable chat.`}
                    </p>
                  </div>
                  <ChevronDown className="size-4 text-muted-foreground rotate-[-90deg]" />
                </div>
              </div>
            </Link>
          )}
        </>
      )}
    </div>
  )
}
