"use client"

import { ArrowLeftIcon, ChevronDown } from "lucide-react"
import Link from "next/link"
import { useCallback, useRef, useState } from "react"
import type { InboxItemRead } from "@/client"
import { agentSessionsForkSession } from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { useGetChatVercel } from "@/hooks/use-chat"
import { useChatReadiness } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface InboxDetailHeaderProps {
  title: string
  status?: string
  workflowHref?: string
  onBack?: () => void
  backLabel?: string
}

function InboxDetailHeader({
  title,
  status,
  workflowHref,
  onBack,
  backLabel,
}: InboxDetailHeaderProps) {
  return (
    <div className="flex h-12 shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-2">
        {onBack && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onBack}
            className="gap-2 -ml-2"
          >
            <ArrowLeftIcon className="size-4" />
            <span className="text-xs">Back to {backLabel}</span>
          </Button>
        )}
        {workflowHref ? (
          <Link
            href={workflowHref}
            className="text-sm font-medium hover:underline"
          >
            {title}
          </Link>
        ) : (
          <span className="text-sm font-medium">{title}</span>
        )}
        {status && (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {status}
          </span>
        )}
      </div>
    </div>
  )
}

interface InboxDetailProps {
  sessionId: string
  /** The original parent session ID (from the inbox item) - used for forking */
  parentSessionId: string
  item: InboxItemRead
  /** Called after successfully forking, passes the forked session ID and the message to send */
  onForked?: (forkedSessionId: string, pendingMessage: string) => void
  /** Message to send immediately (passed from parent after fork) */
  pendingMessage?: string
  /** Called when the pending message has been sent */
  onPendingMessageSent?: () => void
  onBack?: () => void
  backLabel?: string
}

export function InboxDetail({
  sessionId,
  parentSessionId,
  item,
  onForked,
  pendingMessage,
  onPendingMessageSent,
  onBack,
  backLabel,
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

  // Use workflow alias from item if available, otherwise fall back to chat title
  const displayTitle = item.workflow?.alias || chat?.title || item.title

  if (chatLoading || chatReadyLoading) {
    return (
      <div className="flex h-full flex-col">
        <InboxDetailHeader title="Loading..." />
        <div className="flex flex-1 items-center justify-center">
          <CenteredSpinner />
        </div>
      </div>
    )
  }

  if (chatError || !chat) {
    return (
      <div className="flex h-full flex-col">
        <InboxDetailHeader
          title="Error"
          onBack={onBack}
          backLabel={backLabel}
        />
        <div className="flex flex-1 items-center justify-center">
          <span className="text-sm text-red-500">Failed to load session</span>
        </div>
      </div>
    )
  }

  // Check if chat is ready (model configured)
  if (!chatReady || !modelInfo) {
    return (
      <div className="flex h-full flex-col">
        <InboxDetailHeader
          title={displayTitle}
          onBack={onBack}
          backLabel={backLabel}
        />
        <div className="flex flex-1 flex-col items-center justify-center gap-4 p-4">
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
      </div>
    )
  }

  // Build workflow link if available
  const workflowHref = item.workflow?.id
    ? `/workspaces/${workspaceId}/workflows/${item.workflow.id}`
    : undefined

  return (
    <div className="flex h-full flex-col">
      <InboxDetailHeader
        title={displayTitle}
        workflowHref={workflowHref}
        onBack={onBack}
        backLabel={backLabel}
      />

      {/* Chat interface - first message forks to new session, then continues normally */}
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
        className="min-h-0 flex-1"
        modelInfo={modelInfo}
        toolsEnabled={false}
        onBeforeSend={isForkedSession ? undefined : handleFork}
        pendingMessage={pendingMessage}
        onPendingMessageSent={onPendingMessageSent}
      />
    </div>
  )
}
