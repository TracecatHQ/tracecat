"use client"

import { useSearchParams } from "next/navigation"
import { ChatInterface } from "@/components/chat/chat-interface"

export function CaseChat({
  caseId,
  isChatOpen,
}: {
  caseId: string
  isChatOpen: boolean
}) {
  const searchParams = useSearchParams()
  const chatId = searchParams?.get("chatId") ?? undefined

  if (!isChatOpen) {
    return null
  }
  return (
    <div className="flex h-full flex-col">
      <ChatInterface chatId={chatId} entityType="case" entityId={caseId} />
    </div>
  )
}
