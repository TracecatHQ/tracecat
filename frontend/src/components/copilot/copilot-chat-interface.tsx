"use client"

import { ChatInterface } from "@/components/chat/chat-interface"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Main workspace-level chat interface.
 *
 * Uses the workspace ID as the entity ID for workspace-level chat.
 * Supports agent presets and custom tool selection just like case chat.
 */
export function CopilotChatInterface() {
  const workspaceId = useWorkspaceId()

  return (
    <div className="flex h-full flex-col">
      <ChatInterface
        entityType="copilot"
        entityId={workspaceId}
        bodyClassName="px-2"
      />
    </div>
  )
}
