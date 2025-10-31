"use client"

import { formatDistanceToNow } from "date-fns"
import { ChevronDown, MessageSquare, Plus } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type { ChatEntity, ChatRead } from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
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
import { useCreateChat, useGetChatVercel, useListChats } from "@/hooks/use-chat"
import type { ModelInfo } from "@/lib/chat"
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

  /* chat readiness -------------------- */
  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReason,
    modelInfo,
  } = useChatReadiness()

  // Set the first chat as selected when chats are loaded and no chat is selected
  useEffect(() => {
    if (chats && chats.length > 0 && !selectedChatId) {
      const firstChatId = chats[0].id
      setSelectedChatId(firstChatId)
      onChatSelect?.(firstChatId)
    }
  }, [chats, selectedChatId, onChatSelect])

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
    <div className="flex h-full min-h-0 flex-col">
      {/* Chat Header */}
      <div className="px-4 py-2">
        <div className="flex items-center justify-between">
          {/* Unified New-chat / History dropdown */}
          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" className="px-2">
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
          </div>
        </div>
      </div>

      {/* Chat Body */}
      <div className="flex flex-1 min-h-0 flex-col">
        <ChatBody
          chatId={selectedChatId}
          workspaceId={workspaceId}
          entityType={entityType}
          entityId={entityId}
          chatReady={chatReady}
          chatReadyLoading={chatReadyLoading}
          chatReason={chatReason}
          modelInfo={modelInfo}
        />
      </div>
    </div>
  )
}

interface ChatBodyProps {
  chatId?: string
  workspaceId: string
  entityType: ChatEntity
  entityId: string
  chatReady: boolean
  chatReadyLoading: boolean
  chatReason?: string
  modelInfo?: ModelInfo
}

function ChatBody({
  chatId,
  workspaceId,
  entityType,
  entityId,
  chatReady,
  chatReadyLoading,
  chatReason,
  modelInfo,
}: ChatBodyProps) {
  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId,
    workspaceId,
  })

  if (chatError) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-red-500">Failed to load chat</span>
      </div>
    )
  }

  // Render loading state while checking if chat is selected
  if (!chatId || chatReadyLoading || chatLoading || !chat) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  // Render active chat session when ready
  if (!chatReady || !modelInfo) {
    // Render configuration required state
    return (
      <>
        <NoMessages />
        <Link
          href="/organization/settings/agent"
          className="block rounded-md border border-border bg-gradient-to-r from-muted/30 to-muted/50 p-4 backdrop-blur-sm transition-all duration-200 hover:from-muted/40 hover:to-muted/60"
        >
          <div className="p-4">
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <h4 className="mb-1 text-sm font-medium text-foreground">
                  {chatReason === "no_model" && "No default model"}
                  {chatReason === "no_credentials" && "Missing credentials"}
                </h4>
                <p className="text-xs text-muted-foreground">
                  {chatReason === "no_model" &&
                    "Select a default model in agent settings to enable chat."}
                  {chatReason === "no_credentials" &&
                    `Configure ${modelInfo?.provider || "model provider"} credentials in agent settings to enable chat.`}
                </p>
              </div>
              <ChevronDown className="size-4 rotate-[-90deg] text-muted-foreground" />
            </div>
          </div>
        </Link>
      </>
    )
  }

  return (
    <ChatSessionPane
      chat={chat}
      workspaceId={workspaceId}
      entityType={entityType}
      entityId={entityId}
      placeholder={`Ask about this ${entityType}...`}
      className="flex-1 min-h-0"
      modelInfo={modelInfo}
    />
  )
}
