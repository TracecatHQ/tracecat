"use client"

import { useCallback, useRef, useState } from "react"
import { agentSessionsForkSession } from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { CenteredSpinner } from "@/components/loading/spinner"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentPreset,
  useAgentPresetVersion,
} from "@/hooks/use-agent-presets"
import { useGetChatVercel } from "@/hooks/use-chat"
import { type InboxSessionItem, isLiveAgentStatus } from "@/lib/agents"
import { getModelUnavailableMessage } from "@/lib/chat-readiness"
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

  // Show the model the session runs on: its preset's, where a fork uses its
  // source session's preset, as the backend does. Otherwise the default.
  const forkSourceId =
    chat && "forked_from_session_id" in chat
      ? (chat.forked_from_session_id ?? undefined)
      : undefined
  const { chat: forkSource, chatLoading: forkSourceLoading } = useGetChatVercel(
    { chatId: forkSourceId, workspaceId }
  )
  const presetSession = forkSourceId ? forkSource : chat
  const presetId = presetSession?.agent_preset_id ?? null
  const presetVersionId = presetSession?.agent_preset_version_id ?? null
  const { preset, presetIsLoading } = useAgentPreset(workspaceId, presetId, {
    enabled: Boolean(presetId) && !presetVersionId,
  })
  const { presetVersion, presetVersionIsLoading } = useAgentPresetVersion(
    workspaceId,
    presetId,
    presetVersionId
  )
  const presetModel = presetVersionId ? presetVersion : preset

  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReadyReason,
    modelInfo,
  } = useChatReadiness(
    workspaceId,
    presetModel
      ? {
          modelOverride: {
            name: presetModel.model_name,
            provider: presetModel.model_provider,
            baseUrl: presetModel.base_url ?? null,
          },
        }
      : undefined
  )

  // Check if the current session is already a forked session
  // (sessionId differs from parentSessionId when viewing a fork)
  const isForkedSession = sessionId !== parentSessionId

  // Block input when there are pending approvals and we haven't forked yet
  // User must make an approval decision before they can send messages
  const hasPendingApprovals = session.pendingApprovalCount > 0
  // Without a usable model the transcript and approvals stay visible; only
  // new messages are blocked.
  const modelUnavailable = !chatReady || !modelInfo
  const approvalsBlockInput = !isForkedSession && hasPendingApprovals
  const inputDisabled = modelUnavailable || approvalsBlockInput
  const resume = isLiveAgentStatus(session.derivedStatus)

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

  if (
    chatLoading ||
    chatReadyLoading ||
    forkSourceLoading ||
    presetIsLoading ||
    presetVersionIsLoading
  ) {
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

  // The persisted last_error banner is rendered by ChatSessionPane itself (from
  // the session's last_error), so the inbox detail doesn't duplicate it here.
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
      modelInfo={modelInfo ?? undefined}
      toolsEnabled={false}
      onBeforeSend={isForkedSession ? undefined : handleFork}
      pendingMessage={pendingMessage}
      onPendingMessageSent={onPendingMessageSent}
      inputDisabled={inputDisabled}
      inputDisabledPlaceholder={
        modelUnavailable
          ? getModelUnavailableMessage(chatReadyReason)
          : "Make an approval decision to continue..."
      }
      // Re-arm the SDK reconnect when an externally resolved approval moves
      // the session from PENDING_APPROVAL to RUNNING.
      resume={resume}
    />
  )
}
