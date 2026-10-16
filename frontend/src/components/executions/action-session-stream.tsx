"use client"

import { useChat } from "@ai-sdk/react"
import { type ChatStatus, DefaultChatTransport, type UIMessage } from "ai"
import { MessageCircle } from "lucide-react"
import type { ReactNode } from "react"
import { useMemo } from "react"
import type { Session_Any_ } from "@/client"
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import { AgentTurn, groupThread } from "@/components/chat/agent-turn"
import { AgentRunningLine, MessageParts } from "@/components/chat/message-part"
import { Spinner } from "@/components/loading/spinner"
import { toast } from "@/components/ui/use-toast"
import { parseChatError } from "@/hooks/use-chat"
import {
  ChatVerbosityContext,
  useChatVerbosityPreference,
} from "@/hooks/use-chat-verbosity"
import { isUIMessageArray } from "@/lib/agents"
import { getBaseUrl } from "@/lib/api"
import { collectCancelledTurns, transformMessages } from "@/lib/chat"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Render a completed agent transcript or reconnect to its live stream. */
export function ActionSessionStream({ session }: { session: Session_Any_ }) {
  const messages = isUIMessageArray(session.events) ? session.events : undefined
  const thread = useMemo(
    () => (messages ? transformMessages(messages) : []),
    [messages]
  )

  if (messages && messages.length > 0) {
    return (
      <ActionSessionShell>
        <Conversation className="flex-1">
          <ConversationContent>
            <ActionSessionThread messages={thread} />
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
      console.error("Error in Vercel chat:", error)
      toast({
        title: "Chat error",
        description: friendlyMessage,
      })
    },
  })

  const thread = useMemo(() => transformMessages(messages), [messages])
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
            <ActionSessionThread messages={thread} status={status} />
            <AgentRunningLine status={status} messages={thread} />
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      )}
    </ActionSessionShell>
  )
}

/**
 * A session's transformed messages, each agent turn folded once it finishes,
 * as in chat.
 */
function ActionSessionThread({
  messages,
  status,
}: {
  messages: UIMessage[]
  status?: ChatStatus
}) {
  const blocks = useMemo(() => groupThread(messages), [messages])
  const { cancelledTurnMessageIds, interruptedToolCallIds } = useMemo(
    () => collectCancelledTurns(messages),
    [messages]
  )
  const lastMessageId = messages.at(-1)?.id
  const isRunning = status === "streaming" || status === "submitted"
  return blocks.map((block, blockIndex) => {
    if (block.kind === "prompt") {
      return (
        <div key={block.message.id}>
          <MessageParts
            message={block.message}
            status={status}
            isLastMessage={block.message.id === lastMessageId}
            turnCancelled={cancelledTurnMessageIds.has(block.message.id)}
            interruptedToolCallIds={interruptedToolCallIds}
          />
        </div>
      )
    }
    return (
      <div key={`turn-${block.startIndex}`}>
        <AgentTurn
          messages={block.messages}
          startedAt={block.startedAt}
          status={status}
          isLive={isRunning && blockIndex === blocks.length - 1}
          lastMessageId={lastMessageId}
          cancelledMessageIds={cancelledTurnMessageIds}
          interruptedToolCallIds={interruptedToolCallIds}
        />
      </div>
    )
  })
}

function ActionSessionShell({
  status,
  children,
}: {
  status?: string
  children: ReactNode
}) {
  const [verbosity] = useChatVerbosityPreference()
  return (
    <ChatVerbosityContext.Provider value={verbosity}>
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
        <div className="mb-8 flex min-h-[160px] flex-1 flex-col">
          {children}
        </div>
      </div>
    </ChatVerbosityContext.Provider>
  )
}
