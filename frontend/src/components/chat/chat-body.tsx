"use client"

import type { ChatOnDataCallback, ChatStatus, UIMessage } from "ai"
import { ArrowRight, ChevronDown } from "lucide-react"
import Link from "next/link"
import type {
  AgentPresetRead,
  AgentSessionEntity,
  AgentSessionsGetSessionVercelResponse,
} from "@/client"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatEmptyHero } from "@/components/chat/chat-empty-hero"
import {
  AGENT_EXECUTE_REQUIRED_MESSAGE,
  ChatSessionPane,
} from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { parseChatError } from "@/hooks/use-chat"
import {
  type ChatModelNotice,
  getModelUnavailableMessage,
  getWorkspaceModelNotice,
  NO_DEFAULT_MODEL_MESSAGE,
} from "@/lib/chat-readiness"
import { getApiErrorDetail } from "@/lib/errors"
import { useChatReadiness } from "@/lib/hooks"
import type { ChatSurface } from "@/types/chat-surface"

const NOOP = () => {}

export type PresetConfigLike = Pick<
  AgentPresetRead,
  "model_name" | "model_provider" | "base_url"
>

export interface ChatBodyProps {
  chatId?: string
  workspaceId: string
  entityType: AgentSessionEntity
  entityId: string
  placeholder: string
  chat?: AgentSessionsGetSessionVercelResponse
  chatLoading: boolean
  chatError: unknown
  onRetryChatLoad: () => void
  selectedPreset?: PresetConfigLike
  selectedPresetConfigError?: unknown
  toolsEnabled: boolean
  /** Whether `@` offers agent presets on this surface. */
  agentMentionsSupported: boolean
  mcpEnabled: boolean
  draftMode: boolean
  presetSelector?: {
    label: string
    selectedPresetId: string | null
    onSelect: (presetId: string | null) => Promise<boolean>
    disabled?: boolean
    showSpinner?: boolean
  }
  onCreateSessionBeforeSend?: (
    messageText: string,
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => Promise<string | null>
  onCreateSessionForSync?: (
    draftText: string,
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => Promise<void>
  syncDraft?: string
  draftInputDisabled: boolean
  pendingMessage: string | null
  onPendingMessageSent: () => void
  surface: ChatSurface
  onMessagesChange?: (messages: UIMessage[]) => void
  onData?: ChatOnDataCallback<UIMessage>
  onStatusChange?: (status: ChatStatus) => void
}

export function ChatBody({
  chatId,
  workspaceId,
  entityType,
  entityId,
  placeholder,
  chat,
  chatLoading,
  chatError,
  onRetryChatLoad,
  selectedPreset,
  selectedPresetConfigError,
  toolsEnabled,
  agentMentionsSupported,
  mcpEnabled,
  draftMode,
  presetSelector,
  onCreateSessionBeforeSend,
  onCreateSessionForSync,
  syncDraft,
  draftInputDisabled,
  pendingMessage,
  onPendingMessageSent,
  surface,
  onMessagesChange,
  onData,
  onStatusChange,
}: ChatBodyProps) {
  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReadyReason,
    modelInfo,
  } = useChatReadiness(
    workspaceId,
    selectedPreset
      ? {
          modelOverride: {
            name: selectedPreset.model_name,
            provider: selectedPreset.model_provider,
            baseUrl: selectedPreset.base_url ?? null,
          },
        }
      : undefined
  )
  const cannotExecuteAgents = useScopeCheck("agent:execute") === false

  // A failed refetch keeps the loaded transcript; only a session that never
  // loaded shows the error.
  if (chatError && !chat) {
    return (
      <div className="px-4 pb-2">
        <Alert variant="destructive">
          <AlertTitle>Failed to load chat</AlertTitle>
          <AlertDescription>{parseChatError(chatError)}</AlertDescription>
          <Button
            className="mt-2"
            size="sm"
            variant="outline"
            onClick={onRetryChatLoad}
          >
            Retry
          </Button>
        </Alert>
      </div>
    )
  }

  if (chatReadyLoading || (chatId && chatLoading)) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  if (selectedPresetConfigError) {
    const presetErrorMessage =
      getApiErrorDetail(selectedPresetConfigError) ??
      "Failed to load the pinned preset version for this chat."

    return (
      <>
        <NoMessages />
        <Alert variant="destructive">
          <AlertTitle>Pinned preset unavailable</AlertTitle>
          <AlertDescription>{presetErrorMessage}</AlertDescription>
        </Alert>
      </>
    )
  }

  // Model setup only blocks starting a chat. A role that cannot run agents
  // sees that denial instead, and an existing chat keeps its transcript with
  // the composer disabled.
  const modelUnavailable = (!chatReady || !modelInfo) && !chat?.is_readonly
  if (modelUnavailable && !chatId && !cannotExecuteAgents) {
    const workspaceReason = getWorkspaceModelNotice(chatReadyReason)
    if (surface === "workspace-chat") {
      return (
        <ChatEmptyHero>
          <NoDefaultModelComposer workspaceReason={workspaceReason} />
        </ChatEmptyHero>
      )
    }

    if (workspaceReason) {
      return (
        <>
          <NoMessages />
          <div className="rounded-md border border-border bg-muted/40 p-4">
            <h4 className="mb-1 text-sm font-medium text-foreground">
              {workspaceReason.title}
            </h4>
            <p className="text-xs text-muted-foreground">
              {workspaceReason.message}
            </p>
          </div>
        </>
      )
    }

    return (
      <>
        <NoMessages />
        <Link
          href="/organization/settings/agent"
          className="block rounded-md border border-border bg-muted/40 p-4 transition-colors hover:bg-muted/60"
        >
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <h4 className="mb-1 text-sm font-medium text-foreground">
                No default model
              </h4>
              <p className="text-xs text-muted-foreground">
                {NO_DEFAULT_MODEL_MESSAGE}
              </p>
            </div>
            <ChevronDown className="size-4 rotate-[-90deg] text-muted-foreground" />
          </div>
        </Link>
      </>
    )
  }

  if (!chatId) {
    if (!draftMode || !onCreateSessionBeforeSend) {
      // No session exists and this role cannot create one.
      if (cannotExecuteAgents) {
        return (
          <div className="flex h-full items-center justify-center px-4 text-center text-xs text-muted-foreground">
            <p className="max-w-xs text-pretty">
              {AGENT_EXECUTE_REQUIRED_MESSAGE}
            </p>
          </div>
        )
      }
      return (
        <div className="flex h-full items-center justify-center">
          <CenteredSpinner />
        </div>
      )
    }

    return (
      <ChatSessionPane
        workspaceId={workspaceId}
        entityType={entityType}
        entityId={entityId}
        placeholder={placeholder}
        className="flex-1 min-h-0"
        modelInfo={modelInfo ?? undefined}
        toolsEnabled={toolsEnabled}
        agentMentionsSupported={agentMentionsSupported}
        mcpEnabled={mcpEnabled}
        presetSelector={presetSelector}
        onBeforeSend={onCreateSessionBeforeSend}
        onCreateSessionForSync={onCreateSessionForSync}
        optimisticBeforeSend
        inputDisabled={draftInputDisabled}
        inputDisabledPlaceholder="Creating chat..."
        surface={surface}
        onMessagesChange={onMessagesChange}
        onData={onData}
        onStatusChange={onStatusChange}
      />
    )
  }

  if (!chat) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  return (
    <ChatSessionPane
      chat={chat}
      workspaceId={workspaceId}
      entityType={entityType}
      entityId={entityId}
      placeholder={placeholder}
      className="flex-1 min-h-0"
      modelInfo={modelInfo ?? undefined}
      toolsEnabled={toolsEnabled}
      agentMentionsSupported={agentMentionsSupported}
      mcpEnabled={mcpEnabled}
      presetSelector={presetSelector}
      pendingMessage={pendingMessage ?? undefined}
      onPendingMessageSent={onPendingMessageSent}
      syncDraft={syncDraft}
      inputDisabled={modelUnavailable}
      inputDisabledPlaceholder={getModelUnavailableMessage(chatReadyReason)}
      surface={surface}
      onMessagesChange={onMessagesChange}
      onData={onData}
      onStatusChange={onStatusChange}
    />
  )
}

/**
 * Disabled chat composer shown when no default model is configured. Mirrors the
 * real prompt input but is non-interactive, with a CTA linking to agent
 * settings where a default model can be selected.
 */
function NoDefaultModelComposer({
  workspaceReason,
}: {
  workspaceReason: ChatModelNotice | null
}) {
  return (
    <div className="space-y-3">
      <PromptInput
        onSubmit={NOOP}
        aria-disabled="true"
        className="pointer-events-none select-none [&_[data-slot=input-group]]:rounded-2xl [&_[data-slot=input-group]]:border-muted-foreground/25 [&_[data-slot=input-group]]:shadow-none"
      >
        <PromptInputBody>
          <PromptInputTextarea
            placeholder={
              workspaceReason
                ? "Enable a workspace model to start chatting..."
                : "Select a default model to start chatting..."
            }
            readOnly
            tabIndex={-1}
            aria-disabled="true"
            className="cursor-default"
          />
        </PromptInputBody>
        <PromptInputFooter>
          <PromptInputTools />
          <PromptInputSubmit
            disabled
            aria-disabled="true"
            className="text-muted-foreground/80"
          />
        </PromptInputFooter>
      </PromptInput>
      {workspaceReason ? (
        <p className="text-center text-xs text-muted-foreground">
          {workspaceReason.message}
        </p>
      ) : (
        <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <span>No default model configured.</span>
          <Link
            href="/organization/settings/agent"
            className="inline-flex items-center gap-0.5 font-medium text-foreground underline-offset-4 hover:underline"
          >
            Select one in agent settings
            <ArrowRight className="size-3" />
          </Link>
        </div>
      )}
    </div>
  )
}
