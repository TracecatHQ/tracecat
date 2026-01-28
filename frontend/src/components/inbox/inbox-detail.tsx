"use client"

import { ChevronDown } from "lucide-react"
import Link from "next/link"
import { useCallback, useRef, useState } from "react"
import { agentSessionsForkSession } from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
import { toast } from "@/components/ui/use-toast"
import { useGetChatVercel } from "@/hooks/use-chat"
import type { InboxSessionItem } from "@/lib/agents"
import { useChatReadiness } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface InboxDetailProps {
  sessionId: string
  /** The original parent session ID - used for forking */
  parentSessionId: string
  session: InboxSessionItem
  /** Called after successfully forking, passes the forked session ID and the message to send */
  onForked?: (forkedSessionId: string, pendingMessage: string) => void
  /** Message to send immediately (passed from parent after fork) */
  pendingMessage?: string
  /** Called when the pending message has been sent */
  onPendingMessageSent?: () => void
}

export function InboxDetail({
  sessionId,
  parentSessionId,
  session,
  onForked,
  pendingMessage,
  onPendingMessageSent,
}: InboxDetailProps) {
  const workspaceId = useWorkspaceId()
  const [isForking, setIsForking] = useState(false)
  const forkingRef = useRef(false)

  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: sessionId,
    workspaceId,
  })

  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReason,
    modelInfo,
  } = useChatReadiness()

  // Check if the current session is already a forked session
  // (sessionId differs from parentSessionId when viewing a fork)
  const isForkedSession = sessionId !== parentSessionId

  /**
   * Fork the session and notify parent with the message to send.
   * Parent will switch to the forked session and pass pendingMessage,
   * which the new ChatSessionPane will send on mount.
   */
  const handleFork = useCallback(
    async (messageText: string): Promise<string | null> => {
      // Prevent double-forking with ref (survives re-renders)
      if (forkingRef.current) return null
      if (isForkedSession) return null

      forkingRef.current = true
      setIsForking(true)

      try {
        const forked = await agentSessionsForkSession({
          sessionId: parentSessionId,
          workspaceId,
          requestBody: { entity_type: "approval" },
        })

        // Notify parent to switch to forked session with the pending message
        onForked?.(forked.id, messageText)

        return forked.id
      } catch (error) {
        console.error("Failed to fork session:", error)
        toast({
          variant: "destructive",
          title: "Failed to start conversation",
          description: "Could not create a new session. Please try again.",
        })
        forkingRef.current = false
        return null
      } finally {
        setIsForking(false)
      }
    },
    [isForkedSession, parentSessionId, workspaceId, onForked]
  )

  if (chatLoading || chatReadyLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  if (chatError || !chat) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-sm text-red-500">Failed to load session</span>
      </div>
    )
  }

  // Check if chat is ready (model configured)
  if (!chatReady || !modelInfo) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-4">
        <NoMessages />
        <Link
          href="/organization/settings/agent"
          className="block w-full max-w-md rounded-md border border-border bg-gradient-to-r from-muted/30 to-muted/50 p-4 backdrop-blur-sm transition-all duration-200 hover:from-muted/40 hover:to-muted/60"
        >
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <h4 className="mb-1 text-sm font-medium text-foreground">
                {chatReason === "no_model" && "No default model"}
                {chatReason === "no_credentials" && "Missing credentials"}
              </h4>
              <p className="text-xs text-muted-foreground">
                {chatReason === "no_model" &&
                  "Select a default model in agent settings to enable chat."}
                {chatReason === "no_credentials" &&
                  `Configure ${modelInfo?.provider || "model provider"} credentials in agent settings.`}
              </p>
            </div>
            <ChevronDown className="size-4 rotate-[-90deg] text-muted-foreground" />
          </div>
        </Link>
      </div>
    )
  }

  return (
    <ChatSessionPane
      chat={chat}
      workspaceId={workspaceId}
      entityType={
        chat.entity_type as
          | "case"
          | "workflow"
          | "agent_preset"
          | "agent_preset_builder"
          | "copilot"
          | "approval"
          | undefined
      }
      entityId={chat.entity_id ?? undefined}
      placeholder={
        isForking
          ? "Starting conversation..."
          : isForkedSession
            ? "Continue the conversation..."
            : "Type to ask follow-up questions..."
      }
      className="h-full"
      modelInfo={modelInfo}
      toolsEnabled={false}
      onBeforeSend={isForkedSession ? undefined : handleFork}
      pendingMessage={pendingMessage}
      onPendingMessageSent={onPendingMessageSent}
    />
  )
}
