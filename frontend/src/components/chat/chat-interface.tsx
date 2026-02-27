"use client"

import { ChevronDown, Plus } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type {
  AgentPresetRead,
  AgentSessionEntity,
  AgentSessionsGetSessionVercelResponse,
} from "@/client"
import { AgentPresetMenu } from "@/components/chat/agent-preset-menu"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { useChatReadiness } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface ChatInterfaceProps {
  chatId?: string
  entityType: AgentSessionEntity
  entityId: string
  onChatSelect?: (chatId: string) => void
  bodyClassName?: string
}

type PendingFirstMessage = {
  chatId: string
  text: string
}

export function ChatInterface({
  chatId,
  entityType,
  entityId,
  onChatSelect,
  bodyClassName,
}: ChatInterfaceProps) {
  const workspaceId = useWorkspaceId()
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    chatId
  )
  const [newChatDialogOpen, setNewChatDialogOpen] = useState(false)
  const [autoCreateAttempted, setAutoCreateAttempted] = useState(false)
  const [isCaseDraftChat, setIsCaseDraftChat] = useState(false)
  const [pendingFirstMessage, setPendingFirstMessage] =
    useState<PendingFirstMessage | null>(null)

  // Keep local selection aligned when a parent-driven chatId changes.
  useEffect(() => {
    setSelectedChatId(chatId)
    if (chatId) {
      setIsCaseDraftChat(false)
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

  const presetsEnabled =
    agentAddonsEnabled && (entityType === "case" || entityType === "copilot")

  const {
    presets: presetOptions,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetId: effectivePresetId,
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

  useEffect(() => {
    setAutoCreateAttempted(false)
    setIsCaseDraftChat(false)
    setPendingFirstMessage(null)
  }, [entityType, entityId])

  // Auto-select the first chat when available.
  // For non-case entities we preserve the legacy behavior of creating a chat
  // automatically when none exists.
  useEffect(() => {
    if (!chats || chatsLoading || createChatPending) return

    if (
      chats.length > 0 &&
      !selectedChatId &&
      !(entityType === "case" && isCaseDraftChat)
    ) {
      // Select first existing chat
      const firstChatId = chats[0].id
      setSelectedChatId(firstChatId)
      onChatSelect?.(firstChatId)
    } else if (
      entityType !== "case" &&
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
    isCaseDraftChat,
  ])

  const handleCreateChat = async () => {
    setNewChatDialogOpen(false)

    if (entityType === "case") {
      setIsCaseDraftChat(true)
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

  const handleCreateCaseChatOnFirstSend = async (messageText: string) => {
    if (entityType !== "case" || createChatPending) {
      return null
    }

    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: "case",
        entity_id: entityId,
        agent_preset_id: effectivePresetId,
      })

      setIsCaseDraftChat(false)
      setSelectedChatId(newChat.id)
      setPendingFirstMessage({
        chatId: newChat.id,
        text: messageText,
      })
      onChatSelect?.(newChat.id)
      return newChat.id
    } catch (error) {
      console.error("Failed to create case chat on first message:", error)
      toast({
        title: "Failed to create chat",
        description: parseChatError(error),
        variant: "destructive",
      })
      return null
    }
  }

  const handleSelectChat = (chatId: string) => {
    setIsCaseDraftChat(false)
    setSelectedChatId(chatId)
    onChatSelect?.(chatId)
  }

  // Show loading while chats are loading or being auto-created
  if (
    chatsLoading ||
    (entityType !== "case" && chats && chats.length === 0 && createChatPending)
  ) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Chat Header */}
      <div className="px-4 py-2">
        <div className="flex items-center justify-between">
          {/* Unified New-chat / History dropdown */}
          <div className="flex items-center gap-2">
            <ChatHistoryDropdown
              chats={chats}
              isLoading={chatsLoading}
              error={chatsError}
              selectedChatId={selectedChatId}
              onSelectChat={handleSelectChat}
            />

            {/* (left-side plus removed) */}
          </div>

          {/* Right-side controls: preset selector + actions */}
          <div className="flex items-center gap-1">
            {presetsEnabled && (
              <AgentPresetMenu
                label={presetMenuLabel}
                presets={presetOptions}
                presetsIsLoading={presetsIsLoading}
                presetsError={presetsError}
                selectedPresetId={effectivePresetId}
                disabled={presetMenuDisabled}
                showSpinner={showPresetSpinner}
                noPresetDescription="Use workspace default case agent instructions."
                onSelect={(presetId) => void handlePresetChange(presetId)}
              />
            )}
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
                    {entityType === "case"
                      ? "This opens a fresh case chat draft. A new conversation will be created after you send your first message."
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
          chat={chat}
          chatLoading={chatLoading}
          chatError={chatError}
          selectedPreset={selectedPreset}
          toolsEnabled={!selectedPreset}
          draftMode={
            entityType === "case" && (isCaseDraftChat || chats?.length === 0)
          }
          onCreateSessionBeforeSend={
            entityType === "case" ? handleCreateCaseChatOnFirstSend : undefined
          }
          draftInputDisabled={createChatPending}
          pendingMessage={
            selectedChatId && pendingFirstMessage?.chatId === selectedChatId
              ? pendingFirstMessage.text
              : null
          }
          onPendingMessageSent={() =>
            setPendingFirstMessage((current) =>
              current?.chatId === selectedChatId ? null : current
            )
          }
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
  chat?: AgentSessionsGetSessionVercelResponse
  chatLoading: boolean
  chatError: unknown
  selectedPreset?: AgentPresetRead
  toolsEnabled: boolean
  draftMode: boolean
  onCreateSessionBeforeSend?: (messageText: string) => Promise<string | null>
  draftInputDisabled: boolean
  pendingMessage: string | null
  onPendingMessageSent: () => void
}

function ChatBody({
  chatId,
  workspaceId,
  entityType,
  entityId,
  chat,
  chatLoading,
  chatError,
  selectedPreset,
  toolsEnabled,
  draftMode,
  onCreateSessionBeforeSend,
  draftInputDisabled,
  pendingMessage,
  onPendingMessageSent,
}: ChatBodyProps) {
  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReason,
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

  // Render active chat session when ready
  if (!chatReady || !modelInfo) {
    // Render configuration required state
    return (
      <>
        <NoMessages />
        <Link
          href="/organization/settings/agent"
          className="block rounded-md border border-border bg-gradient-to-r from-muted/30 to-muted/50 p-4 backdrop-blur-sm transition-all duration-200 hover:from-muted/40 hover:to-muted/60"
        >
          <div className="p-4">
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
                    `Configure ${modelInfo?.provider || "model provider"} credentials in agent settings to enable chat.`}
                </p>
              </div>
              <ChevronDown className="size-4 rotate-[-90deg] text-muted-foreground" />
            </div>
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
        placeholder={`Ask about this ${entityType}...`}
        className="flex-1 min-h-0"
        modelInfo={modelInfo}
        toolsEnabled={false}
        onBeforeSend={onCreateSessionBeforeSend}
        inputDisabled={draftInputDisabled}
        inputDisabledPlaceholder="Creating chat..."
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
      placeholder={`Ask about this ${entityType}...`}
      className="flex-1 min-h-0"
      modelInfo={modelInfo}
      toolsEnabled={toolsEnabled}
      pendingMessage={pendingMessage ?? undefined}
      onPendingMessageSent={onPendingMessageSent}
    />
  )
}
