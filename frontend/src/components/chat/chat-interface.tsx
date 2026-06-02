"use client"

import { useQueryClient } from "@tanstack/react-query"
import type { ChatOnDataCallback, UIMessage } from "ai"
import { ArrowRight, ChevronDown, Plus } from "lucide-react"
import Link from "next/link"
import { type ReactNode, useEffect, useState } from "react"
import type {
  AgentPresetRead,
  AgentPresetReadMinimal,
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
import { ChatEmptyHero } from "@/components/chat/chat-empty-hero"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  parseChatError,
  useCreateChat,
  useGetChatVercel,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useChatPresetManager } from "@/hooks/use-chat-preset-manager"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail } from "@/lib/errors"
import { useChatReadiness } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"
import type { ChatSurface } from "@/types/chat-surface"

interface ChatInterfaceProps {
  chatId?: string
  entityType: AgentSessionEntity
  entityId: string
  title?: string
  onChatSelect?: (chatId: string) => void
  bodyClassName?: string
  placeholder?: string
  surface?: ChatSurface
  /** Called on every chat message update; lets parents derive side-panel state. */
  onMessagesChange?: (messages: UIMessage[]) => void
  /** Called for data stream parts as soon as the chat transport receives them. */
  onData?: ChatOnDataCallback<UIMessage>
  /** Called when the selected chat payload changes. */
  onChatChange?: (
    chat: AgentSessionsGetSessionVercelResponse | undefined
  ) => void
  /** Extra slot rendered before the New-chat button in the header. */
  headerActions?: ReactNode
}

type PendingFirstMessage = {
  chatId: string
  text: string
}

type PresetConfigLike = Pick<
  AgentPresetRead,
  "model_name" | "model_provider" | "base_url"
>

const NOOP = () => {}

export function ChatInterface({
  chatId,
  entityType,
  entityId,
  title,
  onChatSelect,
  bodyClassName,
  placeholder,
  surface = "regular",
  onMessagesChange,
  onData,
  onChatChange,
  headerActions,
}: ChatInterfaceProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    chatId
  )
  const [newChatDialogOpen, setNewChatDialogOpen] = useState(false)
  const [autoCreateAttempted, setAutoCreateAttempted] = useState(false)
  const [isDraftChat, setIsDraftChat] = useState(false)
  const [pendingFirstMessage, setPendingFirstMessage] =
    useState<PendingFirstMessage | null>(null)

  // Keep local selection aligned when a parent-driven chatId changes.
  useEffect(() => {
    setSelectedChatId(chatId)
    if (chatId) {
      setIsDraftChat(false)
    }
  }, [chatId])

  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId: workspaceId,
    entityType,
    entityId,
  })

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: selectedChatId,
    workspaceId,
  })
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)

  useEffect(() => {
    onChatChange?.(selectedChatId ? chat : undefined)
  }, [chat, onChatChange, selectedChatId])

  const presetsEnabled =
    agentAddonsEnabled && (entityType === "case" || entityType === "copilot")
  const sessionMcpEnabled = agentAddonsEnabled && entityType === "copilot"
  const inWorkspaceChat = surface === "workspace-chat"
  // Surfaces that defer server-side session creation until the first message,
  // showing a draft composer instead of an eagerly-created empty session.
  const deferSessionCreation = entityType === "case" || inWorkspaceChat

  // Mirror the active workspace-chat session into the URL so sessions are
  // deep-linkable (/chat/:sessionId). Uses replaceState to avoid remounting the
  // chat view, keeping the optimistic first-message flow intact.
  useEffect(() => {
    if (!inWorkspaceChat || typeof window === "undefined") {
      return
    }
    const base = `/workspaces/${workspaceId}/chat`
    const nextPath = selectedChatId ? `${base}/${selectedChatId}` : base
    if (window.location.pathname !== nextPath) {
      const nextUrl = `${nextPath}${window.location.search}`
      window.history.replaceState(window.history.state, "", nextUrl)
    }
  }, [inWorkspaceChat, workspaceId, selectedChatId])

  const {
    presets: presetOptions,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetConfig,
    selectedPresetConfigError,
    selectedPresetVersionIsLoading,
    selectedPresetId: effectivePresetId,
    selectedPresetVersionId,
    handlePresetChange,
    presetMenuLabel,
    presetMenuDisabled,
    showPresetSpinner,
  } = useChatPresetManager({
    workspaceId,
    chat,
    updateChat,
    isUpdatingChat: isUpdating,
    chatLoading,
    selectedChatId,
    enabled: presetsEnabled,
  })
  const activePreset = selectedPresetVersionId
    ? (selectedPresetConfig ?? undefined)
    : selectedPreset

  useEffect(() => {
    setAutoCreateAttempted(false)
    setIsDraftChat(false)
    setPendingFirstMessage(null)
  }, [entityType, entityId])

  // Auto-select the first chat when available.
  // Workspace chat always opens a fresh draft, so it never auto-selects.
  // Surfaces that defer session creation skip the legacy auto-create path.
  useEffect(() => {
    if (!chats || chatsLoading || createChatPending) return

    if (
      chats.length > 0 &&
      !selectedChatId &&
      !inWorkspaceChat &&
      !(entityType === "case" && isDraftChat)
    ) {
      // Select first existing chat
      const firstChatId = chats[0].id
      setSelectedChatId(firstChatId)
      onChatSelect?.(firstChatId)
    } else if (
      !deferSessionCreation &&
      chats.length === 0 &&
      !selectedChatId &&
      !autoCreateAttempted
    ) {
      // Auto-create a chat session immediately
      setAutoCreateAttempted(true)
      createChat({
        title: "Chat 1",
        entity_type: entityType,
        entity_id: entityId,
      })
        .then((newChat) => {
          setSelectedChatId(newChat.id)
          onChatSelect?.(newChat.id)
        })
        .catch((error) => {
          console.error("Failed to auto-create chat:", error)
        })
    }
  }, [
    chats,
    chatsLoading,
    selectedChatId,
    onChatSelect,
    createChat,
    createChatPending,
    entityType,
    entityId,
    autoCreateAttempted,
    isDraftChat,
    inWorkspaceChat,
    deferSessionCreation,
  ])

  const handleCreateChat = async () => {
    setNewChatDialogOpen(false)

    if (deferSessionCreation) {
      setIsDraftChat(true)
      setPendingFirstMessage(null)
      setSelectedChatId(undefined)
      return
    }

    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: entityType,
        entity_id: entityId,
      })
      setSelectedChatId(newChat.id)
      onChatSelect?.(newChat.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    }
  }

  const handleCreateSessionOnFirstSend = async (
    messageText: string,
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => {
    if (!deferSessionCreation || createChatPending) {
      return null
    }

    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: entityType,
        entity_id: entityId,
        tools: selectedTools,
        mcp_integrations: selectedMcpIntegrations,
        agent_preset_id: effectivePresetId,
        agent_preset_version_id: selectedPresetVersionId,
      })

      // Prime the vercel chat cache with the freshly created (empty) session so
      // the session view mounts and sends the pending message immediately,
      // rather than waiting on a fetch of a session we already know is empty.
      queryClient.setQueryData<AgentSessionsGetSessionVercelResponse>(
        ["chat", newChat.id, workspaceId, "vercel"],
        { ...newChat, messages: [] }
      )

      setIsDraftChat(false)
      setSelectedChatId(newChat.id)
      setPendingFirstMessage({
        chatId: newChat.id,
        text: messageText,
      })
      onChatSelect?.(newChat.id)
      return newChat.id
    } catch (error) {
      console.error("Failed to create chat on first message:", error)
      toast({
        title: "Failed to create chat",
        description: parseChatError(error),
        variant: "destructive",
      })
      return null
    }
  }

  const handleSelectChat = (chatId: string) => {
    setIsDraftChat(false)
    setSelectedChatId(chatId)
    onChatSelect?.(chatId)
  }

  // Show loading while chats are loading or being auto-created
  if (
    chatsLoading ||
    (!deferSessionCreation && chats && chats.length === 0 && createChatPending)
  ) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  if (selectedPresetVersionIsLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  // Workspace chat exposes tool + MCP selection so users can add extras
  // alongside the always-on platform defaults. Presets still own their tools.
  const toolsEnabled = !activePreset
  const draftMode =
    deferSessionCreation &&
    (inWorkspaceChat || isDraftChat || chats?.length === 0)
  const presetSelector = presetsEnabled
    ? {
        label: presetMenuLabel,
        presets: presetOptions,
        presetsError,
        presetsIsLoading,
        selectedPresetId: effectivePresetId,
        disabled: presetMenuDisabled,
        showSpinner: showPresetSpinner,
        noPresetDescription: "Use workspace default case agent instructions.",
        onSelect: (presetId: string | null) =>
          void handlePresetChange(presetId),
      }
    : undefined
  const pendingMessageText =
    selectedChatId && pendingFirstMessage?.chatId === selectedChatId
      ? pendingFirstMessage.text
      : null
  const handlePendingMessageSent = () =>
    setPendingFirstMessage((current) =>
      current?.chatId === selectedChatId ? null : current
    )

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Chat Header */}
      <div className="px-4 py-2">
        <div className="flex w-full items-center justify-between">
          {/* Unified New-chat / History dropdown */}
          <div className="flex items-center gap-2">
            {title ? (
              <h1 className="min-w-0 truncate text-sm font-medium">{title}</h1>
            ) : null}
            <ChatHistoryDropdown
              chats={chats}
              isLoading={chatsLoading}
              error={chatsError}
              selectedChatId={selectedChatId}
              onSelectChat={handleSelectChat}
            />
          </div>

          {/* Right-side actions */}
          <div className="flex items-center gap-1">
            {headerActions}
            {/* New chat icon button with tooltip */}
            <AlertDialog
              open={newChatDialogOpen}
              onOpenChange={setNewChatDialogOpen}
            >
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <AlertDialogTrigger asChild>
                    <TooltipTrigger asChild>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="size-6 p-0"
                        disabled={createChatPending}
                      >
                        <Plus className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                  </AlertDialogTrigger>
                  <TooltipContent side="bottom">New chat</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Start a new chat?</AlertDialogTitle>
                  <AlertDialogDescription>
                    {deferSessionCreation
                      ? "This starts a fresh chat. A new conversation is created after you send your first message."
                      : "This will create a new conversation. Your current chat will remain accessible from the conversations menu."}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void handleCreateChat()}>
                    Start new chat
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {/* Chat Body */}
      <div className={cn("flex flex-1 min-h-0 flex-col", bodyClassName)}>
        <ChatBody
          chatId={selectedChatId}
          workspaceId={workspaceId}
          entityType={entityType}
          entityId={entityId}
          placeholder={placeholder ?? `Ask about this ${entityType}...`}
          chat={chat}
          chatLoading={chatLoading}
          chatError={chatError}
          selectedPreset={activePreset}
          selectedPresetConfigError={selectedPresetConfigError}
          toolsEnabled={toolsEnabled}
          agentAddonsEnabled={agentAddonsEnabled}
          mcpEnabled={sessionMcpEnabled}
          draftMode={draftMode}
          presetSelector={presetSelector}
          onCreateSessionBeforeSend={
            deferSessionCreation ? handleCreateSessionOnFirstSend : undefined
          }
          draftInputDisabled={createChatPending}
          pendingMessage={pendingMessageText}
          onPendingMessageSent={handlePendingMessageSent}
          surface={surface}
          onMessagesChange={onMessagesChange}
          onData={onData}
        />
      </div>
    </div>
  )
}

interface ChatBodyProps {
  chatId?: string
  workspaceId: string
  entityType: AgentSessionEntity
  entityId: string
  placeholder: string
  chat?: AgentSessionsGetSessionVercelResponse
  chatLoading: boolean
  chatError: unknown
  selectedPreset?: PresetConfigLike
  selectedPresetConfigError?: unknown
  toolsEnabled: boolean
  agentAddonsEnabled: boolean
  mcpEnabled: boolean
  draftMode: boolean
  presetSelector?: {
    label: string
    presets?: AgentPresetReadMinimal[]
    presetsIsLoading: boolean
    presetsError: unknown
    selectedPresetId: string | null
    onSelect: (presetId: string | null) => void | Promise<void>
    disabled?: boolean
    showSpinner?: boolean
    noPresetDescription?: string
  }
  onCreateSessionBeforeSend?: (
    messageText: string,
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => Promise<string | null>
  draftInputDisabled: boolean
  pendingMessage: string | null
  onPendingMessageSent: () => void
  surface: ChatSurface
  onMessagesChange?: (messages: UIMessage[]) => void
  onData?: ChatOnDataCallback<UIMessage>
}

function ChatBody({
  chatId,
  workspaceId,
  entityType,
  entityId,
  placeholder,
  chat,
  chatLoading,
  chatError,
  selectedPreset,
  selectedPresetConfigError,
  toolsEnabled,
  agentAddonsEnabled,
  mcpEnabled,
  draftMode,
  presetSelector,
  onCreateSessionBeforeSend,
  draftInputDisabled,
  pendingMessage,
  onPendingMessageSent,
  surface,
  onMessagesChange,
  onData,
}: ChatBodyProps) {
  const {
    ready: chatReady,
    loading: chatReadyLoading,
    modelInfo,
  } = useChatReadiness(
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

  if (chatError) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-red-500">Failed to load chat</span>
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

  // Render active chat session when ready
  if (!chatReady || !modelInfo) {
    // Render configuration required state
    if (surface === "workspace-chat") {
      return (
        <ChatEmptyHero>
          <NoDefaultModelComposer />
        </ChatEmptyHero>
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
                Select a default model in agent settings to enable chat.
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
        modelInfo={modelInfo}
        toolsEnabled={toolsEnabled}
        agentAddonsEnabled={agentAddonsEnabled}
        mcpEnabled={mcpEnabled}
        presetSelector={presetSelector}
        onBeforeSend={onCreateSessionBeforeSend}
        optimisticBeforeSend
        inputDisabled={draftInputDisabled}
        inputDisabledPlaceholder="Creating chat..."
        surface={surface}
        onMessagesChange={onMessagesChange}
        onData={onData}
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
      modelInfo={modelInfo}
      toolsEnabled={toolsEnabled}
      agentAddonsEnabled={agentAddonsEnabled}
      mcpEnabled={mcpEnabled}
      presetSelector={presetSelector}
      pendingMessage={pendingMessage ?? undefined}
      onPendingMessageSent={onPendingMessageSent}
      surface={surface}
      onMessagesChange={onMessagesChange}
      onData={onData}
    />
  )
}

/**
 * Disabled chat composer shown when no default model is configured. Mirrors the
 * real prompt input but is non-interactive, with a CTA linking to agent
 * settings where a default model can be selected.
 */
function NoDefaultModelComposer() {
  return (
    <div className="space-y-3">
      <PromptInput
        onSubmit={NOOP}
        aria-disabled="true"
        className="pointer-events-none select-none [&_[data-slot=input-group]]:rounded-2xl [&_[data-slot=input-group]]:border-muted-foreground/25 [&_[data-slot=input-group]]:shadow-none"
      >
        <PromptInputBody>
          <PromptInputTextarea
            placeholder="Select a default model to start chatting..."
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
    </div>
  )
}
