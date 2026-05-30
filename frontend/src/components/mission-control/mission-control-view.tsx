"use client"

import type { UIMessage } from "ai"
import { PanelLeftIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ArtifactPanel } from "@/components/mission-control/artifact-panel"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useRemoveSessionArtifact } from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useMissionControlArtifacts } from "@/hooks/use-mission-control-artifacts"
import { useWorkspaceId } from "@/providers/workspace-id"
import type {
  ArtifactType,
  MissionControlArtifact,
} from "@/types/mission-control"

const EMPTY_MESSAGES: UIMessage[] = []
const EMPTY_ARTIFACTS: MissionControlArtifact[] = []

function sessionArtifacts(
  chat: AgentSessionsGetSessionVercelResponse | undefined
): MissionControlArtifact[] {
  if (!chat || !("artifacts" in chat) || !Array.isArray(chat.artifacts)) {
    return EMPTY_ARTIFACTS
  }
  return chat.artifacts as MissionControlArtifact[]
}

export function MissionControlView() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const canAccessMissionControl = useScopeCheck(
    undefined,
    ["agent:execute", "agent:read"],
    { all: true }
  )
  const { hasEntitlement, isLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  const [messages, setMessages] = useState<UIMessage[]>(EMPTY_MESSAGES)
  const [chat, setChat] = useState<
    AgentSessionsGetSessionVercelResponse | undefined
  >()
  const [isPanelCollapsed, setIsPanelCollapsed] = useState(true)
  const { removeArtifact } = useRemoveSessionArtifact(workspaceId)
  const persistedArtifacts = useMemo(() => sessionArtifacts(chat), [chat])
  const closePersistedArtifact = useCallback(
    (type: ArtifactType, id: string) => {
      if (!chat || !("artifacts" in chat)) {
        return
      }
      void removeArtifact({
        sessionId: chat.id,
        artifactType: type,
        artifactId: id,
      })
    },
    [chat, removeArtifact]
  )
  const artifactsState = useMissionControlArtifacts(messages, {
    enabled: true,
    persistedArtifacts,
    onCloseArtifact: closePersistedArtifact,
  })
  const artifactCount = artifactsState.artifacts.length
  const hasArtifacts = artifactCount > 0

  // Auto-expand on the first artifact arrival of an empty→non-empty transition,
  // and auto-collapse once all artifacts go away. Track previous state via ref
  // so manual collapse persists while artifacts remain.
  const prevHasArtifactsRef = useRef(false)
  useEffect(() => {
    const next = artifactCount > 0
    if (next && !prevHasArtifactsRef.current) {
      setIsPanelCollapsed(false)
    } else if (!next && prevHasArtifactsRef.current) {
      setIsPanelCollapsed(true)
    }
    prevHasArtifactsRef.current = next
  }, [artifactCount])

  const collapsePanel = useCallback(() => setIsPanelCollapsed(true), [])
  const expandPanel = useCallback(() => setIsPanelCollapsed(false), [])

  useEffect(() => {
    if (!isLoading && !agentAddonsEnabled) {
      router.replace("/workspaces")
    }
  }, [agentAddonsEnabled, isLoading, router])

  if (isLoading || canAccessMissionControl === undefined) {
    return <CenteredSpinner />
  }

  if (!canAccessMissionControl) {
    return null
  }

  if (!agentAddonsEnabled) {
    return <CenteredSpinner />
  }

  const showExpandButton = hasArtifacts && isPanelCollapsed

  return (
    <div className="relative flex size-full min-h-0 overflow-hidden">
      <div className="flex h-full min-w-[320px] flex-1 flex-col">
        <ChatInterface
          entityType="copilot"
          entityId={workspaceId}
          bodyClassName="min-h-0"
          placeholder="Ask Mission Control..."
          surface="mission-control"
          onMessagesChange={setMessages}
          onChatChange={setChat}
          headerActions={
            showExpandButton ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="size-6 p-0"
                    onClick={expandPanel}
                    aria-label="Show artifacts"
                  >
                    <PanelLeftIcon className="size-4 -scale-x-100" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">Show artifacts</TooltipContent>
              </Tooltip>
            ) : null
          }
        />
      </div>
      <ArtifactPanel
        artifacts={artifactsState.artifacts}
        activeArtifactKey={artifactsState.activeArtifactKey}
        setActiveArtifactKey={artifactsState.setActiveArtifactKey}
        closeArtifact={artifactsState.closeArtifact}
        workspaceId={workspaceId}
        isCollapsed={isPanelCollapsed}
        onCollapse={collapsePanel}
      />
    </div>
  )
}
