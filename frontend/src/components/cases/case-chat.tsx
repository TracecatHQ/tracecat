"use client"

import { useSearchParams } from "next/navigation"
import { ChatInterface } from "@/components/chat/chat-interface"

export function CaseChat({ caseId }: { caseId: string }) {
  const searchParams = useSearchParams()
  const chatId = searchParams?.get("chatId") ?? undefined

  return (
    <div className="flex h-full flex-col">
      <ChatInterface chatId={chatId} entityType="case" entityId={caseId} />
    </div>
  )
}
