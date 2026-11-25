"use client"

import type { ChatOnDataCallback, ChatStatus, UIMessage } from "ai"
import { Plus } from "lucide-react"
import { type ReactNode, useEffect, useState } from "react"
import type {
  AgentBackendRead,
  AgentSessionEntity,
  AgentSessionsGetSessionVercelResponse,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ChatBody } from "@/components/chat/chat-body"
import type { ChatHistoryScope } from "@/components/chat/chat-history-dropdown"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import {
  parseChatError,
  useAgentBackends,
  useCreateChat,
  useGetChatVercel,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useChatPresetManager } from "@/hooks/use-chat-preset-manager"
import { useEntitlements } from "@/hooks/use-entitlements"
import { INBOX_POLL_MS } from "@/hooks/use-inbox"
import { useQueryClient } from "@/lib/query"
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
  /** Called whenever the chat transport status changes. */
  onStatusChange?: (status: ChatStatus) => void
  /** Called when the selected chat payload changes. */
  onChatChange?: (
    chat: AgentSessionsGetSessionVercelResponse | undefined
  ) => void
  /** Extra slot rendered before the New-chat button in the header. */
  headerActions?: ReactNode
}

/** A draft carried into the session it created: sent, or kept to pick a Slack channel. */
type PendingFirstMessage = {
  chatId: string
  text: string
  then: "send" | "pickChannel"
}

const SERVER_DEFAULT_BACKEND = "server-default"

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
  onStatusChange,
  onChatChange,
  headerActions,
}: ChatInterfaceProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  // The server rejects non-default backends for orgs without the entitlement.
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const backendSelectionEnabled = hasEntitlement("agent_runtime_preview")
  const {
    backends,
    backendsLoading,
    backendsReady,
    backendsError,
    refetchBackends,
  } = useAgentBackends(workspaceId, { enabled: backendSelectionEnabled })
  const backendSelectionLoading =
    entitlementsLoading || (backendSelectionEnabled && backendsLoading)
  const backendSelectionReady =
    !entitlementsLoading && (!backendSelectionEnabled || backendsReady)
  const hasBackendChoice = backendSelectionEnabled && backends.length > 1
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    chatId
  )
  const [newChatBackend, setNewChatBackend] = useState(SERVER_DEFAULT_BACKEND)
  const selectedBackend = backends.some(
    (backend) => backend.id === newChatBackend
  )
    ? newChatBackend
    : SERVER_DEFAULT_BACKEND
  const backendOverride =
    backendSelectionEnabled && selectedBackend !== SERVER_DEFAULT_BACKEND
      ? selectedBackend
      : undefined
  const [newChatDialogOpen, setNewChatDialogOpen] = useState(false)
  const [autoCreateAttempted, setAutoCreateAttempted] = useState(false)
  const [isDraftChat, setIsDraftChat] = useState(false)
  const [pendingFirstMessage, setPendingFirstMessage] =
    useState<PendingFirstMessage | null>(null)
  const [historyScope, setHistoryScope] = useState<ChatHistoryScope>("team")

  // Keep local selection aligned when a parent-driven chatId changes.
  useEffect(() => {
    setSelectedChatId(chatId)
    if (chatId) {
      setIsDraftChat(false)
    }
  }, [chatId])

  // Workspace chat (full page or docked) is where Slack-started sessions land.
  const isWorkspaceChat = entityType === "copilot"
  // "Mine" in workspace chat also lists channel sessions the viewer sent in.
  const mineAcrossChannels = historyScope === "mine" && isWorkspaceChat
  const { chats, chatsLoading, chatsError } = useListChats(
    {
      workspaceId: workspaceId,
      entityType: mineAcrossChannels ? undefined : entityType,
      entityId: mineAcrossChannels ? undefined : entityId,
      createdBy:
        historyScope === "mine" && !mineAcrossChannels ? user?.id : undefined,
      participantId: mineAcrossChannels ? user?.id : undefined,
    },
    {
      // Without a viewer id the "mine" list would widen to every chat.
      enabled: !mineAcrossChannels || user?.id !== undefined,
      pollMs: isWorkspaceChat ? INBOX_POLL_MS : undefined,
    }
  )

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const { chat, chatLoading, chatError, refetchChat } = useGetChatVercel({
    chatId: selectedChatId,
    workspaceId,
  })
  const currentBackendId =
    chat && "backend_id" in chat ? chat.backend_id : "oss"
  const currentBackendName =
    backends.find((backend) => backend.id === currentBackendId)?.name ??
    (currentBackendId === "oss" ? "Open source" : "Unavailable")
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)

  useEffect(() => {
    onChatChange?.(selectedChatId ? chat : undefined)
  }, [chat, onChatChange, selectedChatId])

  // Setting a session preset only means something where the session owns one;
  // preset-builder, approval and workflow sessions get no `@` trigger at all.
  const presetsSupported = entityType === "case" || entityType === "copilot"
  const presetsEnabled = presetsSupported
  // `integration:read` is folded in: the picker lists servers from the MCP
  // integration endpoint, which is guarded by it. Without it the request 403s
  // and the picker would offer to connect a server on a page the role cannot
  // open.
  const canReadIntegrations = useScopeCheck("integration:read")
  // `undefined` while scopes load: creation and its controls wait until the
  // scope resolves; only a resolved denial shows the reason.
  const canExecuteAgents = useScopeCheck("agent:execute")
  const sessionMcpEnabled =
    canReadIntegrations === true && entityType === "copilot"
  const inWorkspaceChat = surface === "workspace-chat"
  // Surfaces that defer server-side session creation until the first message,
  // showing a draft composer instead of an eagerly-created empty session.
  const deferSessionCreation =
    entityType === "case" || inWorkspaceChat || hasBackendChoice

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
    selectedPreset,
    selectedPresetConfig,
    selectedPresetConfigError,
    selectedPresetVersionIsLoading,
    selectedPresetId: effectivePresetId,
    selectedPresetVersionId,
    handlePresetChange,
    getPendingPresetSelection,
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
      !(deferSessionCreation && isDraftChat)
    ) {
      // Prefer the current user's latest writable chat before falling back to
      // the newest teammate session.
      const firstChatId =
        chats.find((candidate) => !candidate.is_readonly)?.id ?? chats[0].id
      setSelectedChatId(firstChatId)
      onChatSelect?.(firstChatId)
    } else if (
      canExecuteAgents === true &&
      !deferSessionCreation &&
      backendSelectionReady &&
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
        backend_id: backendOverride,
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
    backendOverride,
    backendSelectionReady,
    canExecuteAgents,
    isDraftChat,
    inWorkspaceChat,
    deferSessionCreation,
  ])

  const handleCreateChat = async () => {
    if (!backendSelectionReady || createChatPending) return
    if (canExecuteAgents !== true) return
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
        backend_id: backendOverride,
      })
      setSelectedChatId(newChat.id)
      onChatSelect?.(newChat.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    }
  }

  /**
   * Creates the draft's session and selects it, carrying the draft; null when it
   * can't or fails.
   */
  const createDraftSession = async (
    text: string,
    then: PendingFirstMessage["then"],
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => {
    if (
      !deferSessionCreation ||
      !backendSelectionReady ||
      createChatPending ||
      canExecuteAgents !== true
    ) {
      return null
    }

    // Read the selection through the manager: an `@Agent` mention sets the
    // preset moments before this runs, and render state is still behind.
    const pendingPreset = getPendingPresetSelection()

    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: entityType,
        entity_id: entityId,
        backend_id: backendOverride,
        tools: selectedTools,
        mcp_integrations: selectedMcpIntegrations,
        agent_preset_id: pendingPreset.presetId,
        agent_preset_version_id: pendingPreset.versionId,
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
      setPendingFirstMessage({ chatId: newChat.id, text, then })
      onChatSelect?.(newChat.id)
      return newChat.id
    } catch (error) {
      console.error("Failed to create chat from a draft:", error)
      toast({
        title: "Failed to create chat",
        description: parseChatError(error),
        variant: "destructive",
      })
      return null
    }
  }

  const handleCreateSessionOnFirstSend = (
    messageText: string,
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) =>
    createDraftSession(
      messageText,
      "send",
      selectedTools,
      selectedMcpIntegrations
    )

  // Destinations are listed per session, so picking a channel needs one first.
  const handleCreateSessionForSync = async (
    draftText: string,
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => {
    await createDraftSession(
      draftText,
      "pickChannel",
      selectedTools,
      selectedMcpIntegrations
    )
  }

  const handleSelectChat = (chatId: string) => {
    // A kept draft would refill the composer on coming back.
    setPendingFirstMessage((current) =>
      current?.then === "pickChannel" ? null : current
    )
    setIsDraftChat(false)
    setSelectedChatId(chatId)
    onChatSelect?.(chatId)
  }

  const handleHistoryScopeChange = (nextScope: ChatHistoryScope) => {
    setHistoryScope(nextScope)
    if (nextScope === "mine" && chat?.is_readonly) {
      setSelectedChatId(undefined)
    }
  }

  // Show loading while chats are loading or being auto-created
  if (
    chatsLoading ||
    (!selectedChatId && backendSelectionLoading) ||
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
        selectedPresetId: effectivePresetId,
        disabled: presetMenuDisabled,
        showSpinner: showPresetSpinner,
        onSelect: handlePresetChange,
      }
    : undefined
  const carried =
    selectedChatId && pendingFirstMessage?.chatId === selectedChatId
      ? pendingFirstMessage
      : null
  const pendingMessageText = carried?.then === "send" ? carried.text : null
  const syncDraftText =
    carried?.then === "pickChannel" ? carried.text : undefined
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
              workspaceId={workspaceId}
              scope={historyScope}
              onScopeChange={handleHistoryScopeChange}
            />
            {chat?.is_readonly ? (
              <Badge
                variant="outline"
                className="px-1.5 py-0 text-[10px] font-normal text-muted-foreground"
              >
                Read only
              </Badge>
            ) : null}
          </div>

          {/* Right-side actions */}
          <div className="flex items-center gap-1">
            {headerActions}
            {backendSelectionEnabled && selectedChatId && chat ? (
              <Badge variant="outline">{currentBackendName}</Badge>
            ) : null}
            {!selectedChatId && hasBackendChoice && (
              <BackendSelect
                backends={backends}
                value={selectedBackend}
                onChange={setNewChatBackend}
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
                        aria-label="New chat"
                        size="sm"
                        variant="ghost"
                        className="size-6 p-0"
                        disabled={
                          createChatPending ||
                          !backendSelectionReady ||
                          canExecuteAgents !== true
                        }
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
                {hasBackendChoice && (
                  <BackendSelect
                    backends={backends}
                    value={selectedBackend}
                    onChange={setNewChatBackend}
                  />
                )}
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={
                      createChatPending ||
                      !backendSelectionReady ||
                      canExecuteAgents !== true
                    }
                    onClick={() => void handleCreateChat()}
                  >
                    Start new chat
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {backendSelectionEnabled && backendsError && (
        <div className="px-4 pb-2">
          <Alert>
            <AlertTitle>Unable to load chat backends</AlertTitle>
            <AlertDescription>
              Retry before starting a new chat. Existing chats are still
              accessible.
            </AlertDescription>
            <Button
              className="mt-2"
              size="sm"
              variant="outline"
              onClick={() => void refetchBackends()}
            >
              Retry
            </Button>
          </Alert>
        </div>
      )}

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
          onRetryChatLoad={() => void refetchChat()}
          selectedPreset={activePreset}
          selectedPresetConfigError={selectedPresetConfigError}
          toolsEnabled={toolsEnabled}
          agentMentionsSupported={presetsSupported}
          mcpEnabled={sessionMcpEnabled}
          draftMode={draftMode}
          presetSelector={presetSelector}
          onCreateSessionBeforeSend={
            deferSessionCreation ? handleCreateSessionOnFirstSend : undefined
          }
          onCreateSessionForSync={
            deferSessionCreation && inWorkspaceChat
              ? handleCreateSessionForSync
              : undefined
          }
          syncDraft={syncDraftText}
          draftInputDisabled={createChatPending || !backendSelectionReady}
          pendingMessage={pendingMessageText}
          onPendingMessageSent={handlePendingMessageSent}
          surface={surface}
          onMessagesChange={onMessagesChange}
          onData={onData}
          onStatusChange={onStatusChange}
        />
      </div>
    </div>
  )
}

function BackendSelect({
  backends,
  value,
  onChange,
}: {
  backends: AgentBackendRead[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Select
      value={value}
      onValueChange={(next) => {
        if (
          next === SERVER_DEFAULT_BACKEND ||
          backends.some((backend) => backend.id === next)
        ) {
          onChange(next)
        }
      }}
    >
      <SelectTrigger aria-label="Backend (dev)" className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectItem value={SERVER_DEFAULT_BACKEND}>Server default</SelectItem>
          {backends.map((backend) => (
            <SelectItem key={backend.id} value={backend.id}>
              {backend.name}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
