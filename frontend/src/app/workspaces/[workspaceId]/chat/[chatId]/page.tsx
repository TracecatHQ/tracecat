"use client"

import { useParams } from "next/navigation"
import { WorkspaceChatView } from "@/components/workspace-chat/workspace-chat-view"

/**
 * Deep link to a single workspace chat session: /chat/:chatId.
 *
 * The `chatId` route segment is the chat session id (a.k.a. sessionId); the
 * chat client refers to it as `chatId` throughout, so we keep that name here.
 */
export default function WorkspaceChatSessionPage() {
  const params = useParams<{ chatId: string }>()
  return <WorkspaceChatView chatId={params?.chatId} />
}
