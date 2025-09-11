import { ChatInterface } from "@/components/chat/chat-interface"

export function CaseChat({
  caseId,
  isChatOpen,
}: {
  caseId: string
  isChatOpen: boolean
}) {
  return (
    <div className="h-full flex flex-col">
      {isChatOpen && <ChatInterface entityType="case" entityId={caseId} />}
    </div>
  )
}
