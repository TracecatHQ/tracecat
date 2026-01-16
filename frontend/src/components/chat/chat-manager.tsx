"use client"

import { useState } from "react"
import type { AgentSessionEntity } from "@/client"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { ChatInterface } from "./chat-interface"
import { ChatList } from "./chat-list"

interface ChatManagerProps {
  workspaceId: string
  entityType: AgentSessionEntity
  entityId: string
  defaultChatId?: string
  className?: string
}

export function ChatManager({
  workspaceId,
  entityType,
  entityId,
  defaultChatId,
  className,
}: ChatManagerProps) {
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    defaultChatId
  )

  const handleChatSelect = (chatId: string) => {
    setSelectedChatId(chatId)
  }

  const handleNewChat = (chatId: string) => {
    setSelectedChatId(chatId)
  }

  return (
    <div className={className}>
      <ResizablePanelGroup direction="horizontal" className="h-full rounded-lg">
        {/* Chat List Panel */}
        <ResizablePanel defaultSize={30} minSize={25} maxSize={50}>
          <ChatList
            workspaceId={workspaceId}
            entityType={entityType}
            entityId={entityId}
            selectedChatId={selectedChatId}
            onChatSelect={handleChatSelect}
            onNewChat={handleNewChat}
            className="h-full border-0 shadow-none"
          />
        </ResizablePanel>

        <ResizableHandle />

        {/* Chat Interface Panel */}
        <ResizablePanel defaultSize={70} minSize={50}>
          {selectedChatId ? (
            <ChatInterface
              chatId={selectedChatId}
              entityType={entityType}
              entityId={entityId}
            />
          ) : (
            <div className="h-full flex items-center justify-center border border-dashed border-border rounded-lg">
              <div className="text-center text-muted-foreground">
                <p className="text-sm">Select a chat from the list</p>
                <p className="text-xs mt-1">
                  or create a new one to get started
                </p>
              </div>
            </div>
          )}
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  )
}
