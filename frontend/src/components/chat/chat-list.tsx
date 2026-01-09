"use client"

import { MessageCircle, Plus } from "lucide-react"
import { useState } from "react"
import { $AgentSessionEntity, type AgentSessionEntity } from "@/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCreateChat, useListChats } from "@/hooks/use-chat"
import { isAgentSessionEntity } from "@/lib/chat"
import { cn } from "@/lib/utils"

const CHAT_ENTITIES = $AgentSessionEntity.enum

interface ChatListProps {
  workspaceId: string
  entityType?: AgentSessionEntity
  entityId?: string
  selectedChatId?: string
  onChatSelect?: (chatId: string) => void
  onNewChat?: (chatId: string) => void
  className?: string
}

export function ChatList({
  workspaceId,
  entityType,
  entityId,
  selectedChatId,
  onChatSelect,
  onNewChat,
  className,
}: ChatListProps) {
  const [isCreating, setIsCreating] = useState(false)

  // Fetch chats for the entity
  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId,
    entityType,
    entityId,
  })

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const handleNewChat = async () => {
    if (!entityType || !entityId) return

    // Validate that entityType is a valid AgentSessionEntity value
    if (!isAgentSessionEntity(entityType)) {
      console.error(
        `Invalid entity type: ${entityType}. Expected one of: ${CHAT_ENTITIES.join(
          ", "
        )}`
      )
      return
    }

    setIsCreating(true)
    try {
      const result = await createChat({
        title: `New Chat - ${new Date().toLocaleString()}`,
        entity_type: entityType,
        entity_id: entityId,
      })

      // Notify parent about new chat
      onNewChat?.(result.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    } finally {
      setIsCreating(false)
    }
  }

  if (chatsError) {
    return (
      <Card className={className}>
        <CardContent className="p-4">
          <p className="text-sm text-red-600">Failed to load chats</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={className}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <MessageCircle className="h-4 w-4" />
            Chats ({chats?.length || 0})
          </CardTitle>
          {entityType && entityId && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleNewChat}
              disabled={isCreating || createChatPending}
              className="h-7 px-2"
            >
              <Plus className="h-3 w-3 mr-1" />
              New
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {chatsLoading ? (
          <div className="space-y-2 p-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : chats && chats.length > 0 ? (
          <div className="space-y-1 p-3">
            {chats.map((chat) => (
              <button
                key={chat.id}
                onClick={() => onChatSelect?.(chat.id)}
                className={cn(
                  "w-full text-left p-2 rounded-md text-sm transition-colors",
                  "hover:bg-muted/50",
                  selectedChatId === chat.id
                    ? "bg-muted border border-border"
                    : ""
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <p className="font-medium truncate">{chat.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(chat.created_at).toLocaleString()}
                    </p>
                  </div>
                  <MessageCircle className="h-3 w-3 text-muted-foreground mt-0.5 flex-shrink-0" />
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="p-6 text-center text-sm text-muted-foreground">
            <MessageCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
            {entityType && entityId ? (
              <div>
                <p>No chats yet</p>
                <p className="text-xs mt-1">
                  Create your first chat to get started
                </p>
              </div>
            ) : (
              <p>Select an entity to view chats</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
