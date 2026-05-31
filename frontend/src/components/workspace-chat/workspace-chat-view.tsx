"use client"

import { useQueryClient } from "@tanstack/react-query"
import type { ChatOnDataCallback, UIMessage } from "ai"
import { PanelLeftIcon } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { AgentSessionsGetSessionVercelResponse } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatInterface } from "@/components/chat/chat-interface"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ArtifactPanel } from "@/components/workspace-chat/artifacts/artifact-panel"
import { invalidateArtifactQueries } from "@/components/workspace-chat/artifacts/artifact-registry"
import { useRemoveSessionArtifact } from "@/hooks/use-chat"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceChatArtifacts } from "@/hooks/use-workspace-chat-artifacts"
import { useWorkspaceId } from "@/providers/workspace-id"
import {
  ARTIFACT_DATA_PART_TYPE,
  type ArtifactType,
  parseWorkspaceChatArtifactStreamPart,
  type WorkspaceChatArtifact,
  type WorkspaceChatArtifactStreamPart,
} from "@/types/workspace-chat-artifacts"

const EMPTY_MESSAGES: UIMessage[] = []
const EMPTY_ARTIFACTS: WorkspaceChatArtifact[] = []
const ARTIFACT_QUERY_PARAM = "artifact"
const ARTIFACT_TAB_QUERY_PARAM = "tab"
const ARTIFACT_TYPES = new Set<ArtifactType>([
  "case",
  "workflow",
  "run",
  "table",
  "agent",
  "alert",
  "integration",
  "secret",
  "generic",
])

function parseArtifactQueryValue(value: string | null): string | null {
  if (!value) {
    return null
  }
  const separatorIndex = value.indexOf(":")
  if (separatorIndex <= 0 || separatorIndex === value.length - 1) {
    return null
  }
  const type = value.slice(0, separatorIndex)
  if (!ARTIFACT_TYPES.has(type as ArtifactType)) {
    return null
  }
  return value
}

function parseArtifactTabQueryValue(value: string | null): string | null {
  if (!value || !/^[a-z0-9-]+$/.test(value)) {
    return null
  }
  return value
}

function sessionArtifacts(
  chat: AgentSessionsGetSessionVercelResponse | undefined
): WorkspaceChatArtifact[] {
  if (!chat || !("artifacts" in chat) || !Array.isArray(chat.artifacts)) {
    return EMPTY_ARTIFACTS
  }
  return chat.artifacts as WorkspaceChatArtifact[]
}

/**
 * Workspace chat surface. When `chatId` is provided (deep link via
 * /chat/:chatId) it opens that session; otherwise it starts a fresh draft.
 *
 * `chatId` is the chat session id (a.k.a. sessionId) — the chat client names
 * it `chatId` everywhere, so the prop matches that convention.
 */
export function WorkspaceChatView({ chatId }: { chatId?: string }) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const initialArtifactKeyRef = useRef(
    parseArtifactQueryValue(searchParams.get(ARTIFACT_QUERY_PARAM))
  )
  const initialArtifactTabRef = useRef(
    parseArtifactTabQueryValue(searchParams.get(ARTIFACT_TAB_QUERY_PARAM))
  )
  const queryClient = useQueryClient()
  const canAccessMissionControl = useScopeCheck(
    undefined,
    ["agent:execute", "agent:read"],
    { all: true }
  )
  const { hasEntitlement, isLoading } = useEntitlements()
  const workspaceChatEnabled = hasEntitlement("workspace_chat")

  const [messages, setMessages] = useState<UIMessage[]>(EMPTY_MESSAGES)
  const [chat, setChat] = useState<
    AgentSessionsGetSessionVercelResponse | undefined
  >()
  const [isPanelCollapsed, setIsPanelCollapsed] = useState(true)
  const [artifactTab, setArtifactTab] = useState<string | null>(
    initialArtifactTabRef.current
  )
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
  const handleArtifactStreamPart = useCallback(
    (part: WorkspaceChatArtifactStreamPart) => {
      switch (part.type) {
        case ARTIFACT_DATA_PART_TYPE:
          invalidateArtifactQueries(
            queryClient,
            workspaceId,
            part.data.artifact
          )
          return
      }
    },
    [queryClient, workspaceId]
  )
  const handleStreamData = useCallback<ChatOnDataCallback<UIMessage>>(
    (dataPart) => {
      const part = parseWorkspaceChatArtifactStreamPart(dataPart)
      if (!part) {
        return
      }
      handleArtifactStreamPart(part)
    },
    [handleArtifactStreamPart]
  )
  const artifactsState = useWorkspaceChatArtifacts(messages, {
    enabled: true,
    initialActiveArtifactKey: initialArtifactKeyRef.current,
    persistedArtifacts,
    onArtifactStreamPart: handleArtifactStreamPart,
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

  useEffect(() => {
    if (chat === undefined && !hasArtifacts) {
      return
    }

    const url = new URL(window.location.href)
    if (artifactsState.activeArtifactKey) {
      url.searchParams.set(
        ARTIFACT_QUERY_PARAM,
        artifactsState.activeArtifactKey
      )
      if (artifactTab) {
        url.searchParams.set(ARTIFACT_TAB_QUERY_PARAM, artifactTab)
      } else {
        url.searchParams.delete(ARTIFACT_TAB_QUERY_PARAM)
      }
    } else {
      url.searchParams.delete(ARTIFACT_QUERY_PARAM)
      url.searchParams.delete(ARTIFACT_TAB_QUERY_PARAM)
    }
    url.hash = ""
    window.history.replaceState(window.history.state, "", url.toString())
  }, [artifactTab, artifactsState.activeArtifactKey, chat, hasArtifacts])

  const collapsePanel = useCallback(() => setIsPanelCollapsed(true), [])
  const expandPanel = useCallback(() => setIsPanelCollapsed(false), [])

  useEffect(() => {
    if (isLoading || canAccessMissionControl === undefined) {
      return
    }
    if (!canAccessMissionControl) {
      router.replace(`/workspaces/${workspaceId}`)
    }
  }, [canAccessMissionControl, isLoading, router, workspaceId])

  if (isLoading || canAccessMissionControl === undefined) {
    return <CenteredSpinner />
  }

  if (!canAccessMissionControl) {
    return <CenteredSpinner />
  }

  if (!workspaceChatEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="mx-auto flex h-full w-full max-w-3xl flex-1 items-center justify-center py-12">
          <EntitlementRequiredEmptyState
            title="Upgrade required"
            description="Chat is unavailable on your current plan."
          />
        </div>
      </div>
    )
  }

  const showExpandButton = hasArtifacts && isPanelCollapsed
  const showArtifactPanel = hasArtifacts && !isPanelCollapsed

  return (
    <ResizablePanelGroup
      direction="horizontal"
      className="relative size-full min-h-0 overflow-hidden"
    >
      <ResizablePanel
        id="workspace-chat"
        order={1}
        defaultSize={showArtifactPanel ? 50 : 100}
        minSize={30}
        className="min-w-0"
      >
        <div className="flex size-full min-w-[320px] flex-col">
          <ChatInterface
            chatId={chatId}
            entityType="copilot"
            entityId={workspaceId}
            bodyClassName="min-h-0"
            placeholder="Ask Tracecat..."
            surface="workspace-chat"
            onMessagesChange={setMessages}
            onData={handleStreamData}
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
      </ResizablePanel>

      {showArtifactPanel ? (
        <>
          <ResizableHandle className="z-10 -mx-1 w-2 bg-transparent after:w-px after:bg-border" />
          <ResizablePanel
            id="workspace-chat-artifacts"
            order={2}
            defaultSize={50}
            minSize={30}
            maxSize={70}
            className="min-w-0"
          >
            <ArtifactPanel
              artifacts={artifactsState.artifacts}
              activeArtifactKey={artifactsState.activeArtifactKey}
              setActiveArtifactKey={artifactsState.setActiveArtifactKey}
              closeArtifact={artifactsState.closeArtifact}
              workspaceId={workspaceId}
              artifactTab={artifactTab}
              setArtifactTab={setArtifactTab}
              onCollapse={collapsePanel}
            />
          </ResizablePanel>
        </>
      ) : null}
    </ResizablePanelGroup>
  )
}
