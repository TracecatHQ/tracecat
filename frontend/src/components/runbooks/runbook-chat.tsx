import { ChatInterface } from "@/components/chat/chat-interface"

export function RunbookChat({
  runbookId,
  isChatOpen,
}: {
  runbookId: string
  isChatOpen: boolean
}) {
  return (
    <div className="h-full flex flex-col">
      {isChatOpen && (
        <ChatInterface entityType="runbook" entityId={runbookId} />
      )}
    </div>
  )
}
