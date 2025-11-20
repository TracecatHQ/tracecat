"use client"

import { ChatInterface } from "@/components/chat/chat-interface"

export function CaseChat({
  caseId,
  isChatOpen,
}: {
  caseId: string
  isChatOpen: boolean
}) {
  if (!isChatOpen) {
    return null
  }
  return (
    <div className="flex h-full flex-col">
      <ChatInterface entityType="case" entityId={caseId} />
    </div>
  )
}
