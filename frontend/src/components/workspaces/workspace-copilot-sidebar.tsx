"use client"

import { ChatInterface } from "@/components/chat/chat-interface"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkspaceCopilotSidebar() {
  const workspaceId = useWorkspaceId()

  return (
    <ResizableSidebar initial={450} min={350} max={700}>
      <div className="flex h-full flex-col">
        <ChatInterface entityType="copilot" entityId={workspaceId} />
      </div>
    </ResizableSidebar>
  )
}
