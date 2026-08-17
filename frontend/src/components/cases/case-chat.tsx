"use client"

import { ChatInterface } from "@/components/chat/chat-interface"
import { useCaseChatSession } from "@/hooks/use-case-chat-session"

/** Render a case-scoped chat synchronized with the selected URL session. */
export function CaseChat({ caseId }: { caseId: string }) {
  const { chatId, openChatSession } = useCaseChatSession()

  return (
    <div className="flex h-full flex-col">
      <ChatInterface
        chatId={chatId}
        entityType="case"
        entityId={caseId}
        onChatSelect={openChatSession}
      />
    </div>
  )
}
