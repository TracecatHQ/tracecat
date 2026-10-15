"use client"

import { ChatInterface } from "@/components/chat/chat-interface"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { useArtifactInvalidation } from "@/hooks/use-artifact-invalidation"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * The workspace chat docked beside a page.
 *
 * It shows no artifact panel, but it still refreshes the page's data when
 * the chat reads or changes an object, the way the full-page chat does.
 */
export function WorkspaceChatSidebar() {
  const workspaceId = useWorkspaceId()
  const { handleStreamData } = useArtifactInvalidation(workspaceId)

  return (
    <ResizableSidebar initial={450} min={350} max={700}>
      <div className="flex h-full flex-col">
        <ChatInterface
          entityType="copilot"
          entityId={workspaceId}
          onData={handleStreamData}
        />
      </div>
    </ResizableSidebar>
  )
}
