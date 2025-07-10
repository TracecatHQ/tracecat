import { Suspense } from "react"
import { ChatManager } from "@/components/chat/chat-manager"
import { CenteredSpinner } from "@/components/loading/spinner"

export default async function ChatPage({
  params,
}: {
  params: Promise<{ workspaceId: string; caseId: string }>
}) {
  const { workspaceId, caseId } = await params

  if (!caseId || !workspaceId) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-sm text-muted-foreground">No case selected</p>
      </div>
    )
  }

  return (
    <Suspense fallback={<CenteredSpinner />}>
      <ChatManager
        workspaceId={workspaceId}
        entityType="case"
        entityId={caseId}
        className="h-full"
      />
    </Suspense>
  )
}
