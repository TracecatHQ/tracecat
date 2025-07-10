import { ChevronDown, ChevronRight, MessageSquare, Plus } from "lucide-react"
import { useEffect, useState } from "react"
import type { ChatRead } from "@/client"
import { ChatInterface } from "@/components/chat/chat-interface"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useCreateChat, useListChats } from "@/hooks/use-chat"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

export function CaseChat({
  caseId,
  isChatOpen,
}: {
  caseId: string
  isChatOpen: boolean
}) {
  const { workspaceId } = useWorkspace()
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [isCollapsed, setIsCollapsed] = useState<boolean>(true)

  // Grab the chats for this case
  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId: workspaceId,
    entityType: "case",
    entityId: caseId,
  })

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  // Set the first chat as selected when chats are loaded
  useEffect(() => {
    if (chats && chats.length > 0 && !selectedChatId) {
      setSelectedChatId(chats[0].id)
    }
  }, [chats, selectedChatId])

  const handleCreateChat = async () => {
    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: "case",
        entity_id: caseId,
      })
      setSelectedChatId(newChat.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    }
  }

  const handleSelectChat = (chatId: string) => {
    setSelectedChatId(chatId)
  }

  const toggleCollapsed = () => {
    setIsCollapsed(!isCollapsed)
  }

  const selectedChat = chats?.find((chat) => chat.id === selectedChatId)

  return (
    <div
      className={cn(
        "w-96 border-l bg-background transition-all duration-300 ease-in-out flex flex-col",
        isChatOpen
          ? "translate-x-0"
          : "translate-x-full absolute right-0 top-0 h-full"
      )}
    >
      {isChatOpen && (
        <>
          {/* Chat Selection Header */}
          <div className="border-b bg-muted/30">
            <div className="flex items-center justify-between p-4 pb-3">
              <Button
                variant="ghost"
                size="sm"
                className="h-auto p-0 hover:bg-transparent"
                onClick={toggleCollapsed}
              >
                <h3 className="text-sm font-semibold flex items-center gap-2">
                  {isCollapsed ? (
                    <ChevronRight className="h-4 w-4" />
                  ) : (
                    <ChevronDown className="h-4 w-4" />
                  )}
                  <MessageSquare className="h-4 w-4" />
                  Chat Sessions
                </h3>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={handleCreateChat}
                disabled={createChatPending}
              >
                <Plus className="h-3 w-3 mr-1" />
                New
              </Button>
            </div>

            {/* Chat List - Collapsible */}
            <div
              className={cn(
                "overflow-hidden transition-all duration-300 ease-in-out",
                isCollapsed ? "max-h-0" : "max-h-32"
              )}
            >
              <div className="px-4 pb-4">
                <ScrollArea className="h-32">
                  {chatsLoading ? (
                    <div className="space-y-2">
                      <div className="h-8 bg-muted animate-pulse rounded" />
                      <div className="h-8 bg-muted animate-pulse rounded" />
                    </div>
                  ) : chatsError ? (
                    <div className="text-sm text-red-600 p-2">
                      Failed to load chats
                    </div>
                  ) : chats && chats.length > 0 ? (
                    <div className="space-y-1">
                      {chats.map((chat: ChatRead) => (
                        <Button
                          key={chat.id}
                          variant={
                            selectedChatId === chat.id ? "secondary" : "ghost"
                          }
                          size="sm"
                          className="w-full justify-start text-left h-auto p-2"
                          onClick={() => handleSelectChat(chat.id)}
                        >
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium truncate">
                              {chat.title}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              {new Date(chat.created_at).toLocaleDateString()}
                            </div>
                          </div>
                          {selectedChatId === chat.id && (
                            <Badge variant="secondary" className="text-xs ml-2">
                              Active
                            </Badge>
                          )}
                        </Button>
                      ))}
                    </div>
                  ) : (
                    <div className="text-sm text-muted-foreground p-2 text-center">
                      No chats yet. Create one to get started!
                    </div>
                  )}
                </ScrollArea>
              </div>
            </div>
          </div>

          {/* Chat Interface */}
          <div className="flex-1 min-h-0">
            {selectedChat ? (
              <ChatInterface
                chatId={selectedChat.id}
                entityType="case"
                entityId={caseId}
              />
            ) : chats && chats.length === 0 ? (
              <div className="flex h-full items-center justify-center p-8">
                <div className="text-center max-w-sm">
                  <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
                  <h4 className="text-sm font-medium text-gray-900 mb-1">
                    No chat sessions
                  </h4>
                  <p className="text-xs text-gray-500 mb-4">
                    Create a chat session to start conversing with the AI
                    assistant about this case.
                  </p>
                  <Button
                    onClick={handleCreateChat}
                    disabled={createChatPending}
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    Create chat session
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        </>
      )}
    </div>
  )
}
