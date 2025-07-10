import { ChatInterface } from "@/components/chat/chat-interface"
import { cn } from "@/lib/utils"

export function CaseChat({
  caseId,
  isChatOpen,
}: {
  caseId: string
  isChatOpen: boolean
}) {
  return (
    <div
      className={cn(
        "w-96 border-l bg-background transition-all duration-300 ease-in-out flex flex-col",
        isChatOpen
          ? "translate-x-0"
          : "translate-x-full absolute right-0 top-0 h-full"
      )}
    >
      {isChatOpen && <ChatInterface entityType="case" entityId={caseId} />}
    </div>
  )
}
