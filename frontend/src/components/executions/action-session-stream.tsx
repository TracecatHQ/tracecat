"use client"

import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import { MessageCircle } from "lucide-react"
import type { ReactNode } from "react"
import { useMemo, useState } from "react"
import type { Session_Any_ } from "@/client"
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import { MessagePart } from "@/components/chat/chat-session-pane"
import { Spinner } from "@/components/loading/spinner"
import { toast } from "@/components/ui/use-toast"
import { parseChatError } from "@/hooks/use-chat"
import { isUIMessageArray } from "@/lib/agents"
import { getBaseUrl } from "@/lib/api"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Render a completed agent transcript or reconnect to its live stream. */
export function ActionSessionStream({ session }: { session: Session_Any_ }) {
  const messages = isUIMessageArray(session.events) ? session.events : undefined

  if (messages && messages.length > 0) {
    return (
      <ActionSessionShell>
        <Conversation className="flex-1">
          <ConversationContent>
            {messages.map(({ id, role, parts }) => (
              <div key={id}>
                {parts?.map((part, partIdx) => (
                  <MessagePart
                    key={`${id}-${partIdx}`}
                    part={part}
                    partIdx={partIdx}
                    id={id}
                    role={role}
                    isLastMessage={id === messages[messages.length - 1].id}
                  />
                ))}
              </div>
            ))}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      </ActionSessionShell>
    )
  }

  return <ActionSessionLiveStream sessionId={session.id} />
}

function ActionSessionLiveStream({ sessionId }: { sessionId: string }) {
  const workspaceId = useWorkspaceId()
  const [_lastError, setLastError] = useState<string | null>(null)

  const transport = useMemo(() => {
    return new DefaultChatTransport({
      credentials: "include",
      prepareReconnectToStreamRequest: ({ id }) => {
        const url = new URL(`/api/agent/sessions/${id}/stream`, getBaseUrl())
        url.searchParams.set("workspace_id", workspaceId)
        return {
          api: url.toString(),
          credentials: "include",
        }
      },
    })
  }, [workspaceId])

  const { messages, status } = useChat({
    id: sessionId,
    resume: true,
    transport,
    onError: (error) => {
      const friendlyMessage = parseChatError(error)
      setLastError(friendlyMessage)
      console.error("Error in Vercel chat:", error)
      toast({
        title: "Chat error",
        description: friendlyMessage,
      })
    },
  })

  const headerStatus = status === "streaming" ? "streaming" : undefined

  return (
    <ActionSessionShell status={headerStatus}>
      {status === "submitted" ? (
        <div className="flex flex-1 items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
          <Spinner className="size-3" />
          <span>Connecting to agent…</span>
        </div>
      ) : (
        <Conversation className="flex-1">
          <ConversationContent>
            {messages.map(({ id, role, parts }) => (
              <div key={id}>
                {parts?.map((part, partIdx) => (
                  <MessagePart
                    key={`${id}-${partIdx}`}
                    part={part}
                    partIdx={partIdx}
                    id={id}
                    role={role}
                    status={status}
                    isLastMessage={id === messages[messages.length - 1].id}
                  />
                ))}
              </div>
            ))}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      )}
    </ActionSessionShell>
  )
}

function ActionSessionShell({
  status,
  children,
}: {
  status?: string
  children: ReactNode
}) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-md border bg-card">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <MessageCircle className="size-3" />
        <span>Session</span>
        {status === "streaming" && (
          <span className="ml-auto flex items-center gap-1 text-[10px] font-medium normal-case text-muted-foreground">
            <Spinner className="size-3" />
            <span>Streaming…</span>
          </span>
        )}
      </div>
      <div className="mb-8 flex min-h-[160px] flex-1 flex-col">{children}</div>
    </div>
  )
}
