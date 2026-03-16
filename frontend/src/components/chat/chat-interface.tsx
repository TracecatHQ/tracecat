"use client"

import { ChevronDown, Loader2, Plus } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type {
  AgentPresetRead,
  AgentPresetVersionReadMinimal,
  AgentSessionEntity,
  AgentSessionsGetSessionVercelResponse,
} from "@/client"
import { AgentPresetVersionSelect } from "@/components/agents/agent-preset-version-select"
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
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
import {
  parseChatError,
  useCreateChat,
  useGetChatVercel,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useChatPresetManager } from "@/hooks/use-chat-preset-manager"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  type AgentCatalogEntry,
  getModelSelectionKey,
  matchesModelSelection,
  useChatReadiness,
} from "@/lib/hooks"
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

type PresetConfigLike = Pick<
  AgentPresetRead,
  | "source_id"
  | "model_name"
  | "model_provider"
  | "base_url"
  | "actions"
  | "namespaces"
>

type ChatSessionWithCompositeSelection =
  AgentSessionsGetSessionVercelResponse & {
    source_id?: string | null
    model_provider?: string | null
    model_name?: string | null
  }

const DEFAULT_CHAT_TOOLS: Partial<Record<AgentSessionEntity, string[]>> = {
  case: [
    "core.cases.get_case",
    "core.cases.list_cases",
    "core.cases.update_case",
    "core.cases.create_comment",
    "core.cases.list_comments",
  ],
  copilot: [
    "core.table.list_tables",
    "core.table.get_table_metadata",
    "core.table.lookup",
    "core.table.search_rows",
    "core.cases.list_cases",
    "core.cases.get_case",
    "core.cases.search_cases",
  ],
}

type ChatModelSelector = {
  label: string
  defaultLabel: string
  defaultProvider?: string | null
  models?: AgentCatalogEntry[]
  modelsError: unknown
  modelsIsLoading: boolean
  selectedModel: AgentCatalogEntry | null
  onSelect: (model: AgentCatalogEntry | null) => void | Promise<void>
  disabled?: boolean
  showSpinner?: boolean
}

type ChatPresetSelector = {
  presets: Pick<AgentPresetRead, "id" | "name">[]
  presetsIsLoading: boolean
  presetsError: unknown
  selectedPresetId: string | null
  onSelect: (presetId: string | null) => void | Promise<void>
  disabled: boolean
  showSpinner: boolean
  versions?: AgentPresetVersionReadMinimal[]
  versionsIsLoading: boolean
  versionsError: unknown
  selectedVersionId: string | null
  currentVersionId: string | null
  onVersionSelect: (versionId: string | null) => void | Promise<void>
  versionDisabled: boolean
}

function getCompositeModelKey(
  model: Pick<AgentCatalogEntry, "source_id" | "model_provider" | "model_name">
): string {
  return getModelSelectionKey(model)
}

function findSelectedModel(
  models: AgentCatalogEntry[] | undefined,
  chat: AgentSessionsGetSessionVercelResponse | undefined
): AgentCatalogEntry | null {
  const selection = chat as ChatSessionWithCompositeSelection | undefined
  if (!selection?.model_provider || !selection?.model_name) {
    return null
  }
  const modelProvider = selection.model_provider
  const modelName = selection.model_name
  return (
    models?.find((model) =>
      matchesModelSelection(model, {
        source_id: selection.source_id ?? null,
        model_provider: modelProvider,
        model_name: modelName,
      })
    ) ?? null
  )
}

function getSessionModelFields(model: AgentCatalogEntry | null): {
  source_id?: string | null
  model_provider?: string | null
  model_name?: string | null
} {
  if (!model) {
    return {}
  }
  return {
    source_id: model.source_id ?? null,
    model_provider: model.model_provider,
    model_name: model.model_name,
  }
}

function getModelSelectorErrorMessage(error: unknown): string {
  if (typeof error === "string") {
    return error
  }
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    typeof (error as { message?: unknown }).message === "string"
  ) {
    return (error as { message: string }).message
  }
  return "Unable to load enabled models."
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
    presets,
    presetsIsLoading,
    presetsError,
    selectedPreset,
    selectedPresetConfig,
    selectedPresetId: effectivePresetId,
    selectedPresetVersionId,
    versions,
    versionsIsLoading,
    versionsError,
    currentPresetVersionId,
    handlePresetChange,
    handlePresetVersionChange,
    presetMenuDisabled,
    showPresetSpinner,
    versionMenuDisabled,
    enabledModels,
    enabledModelsError,
    enabledModelsLoading,
    selectedModel,
    handleModelSelectionChange,
    defaultModelLabel,
    defaultModelProvider,
    modelSelectorLabel,
    modelSelectorDisabled,
    showModelSelectorSpinner,
  } = useChatPresetManager({
    workspaceId,
    chat,
    updateChat,
    isUpdatingChat: isUpdating,
    chatLoading,
    selectedChatId,
    enabled: presetsEnabled,
  })
  const effectiveSelectedModel =
    selectedModel ?? findSelectedModel(enabledModels, chat)
  const activePreset = selectedPresetConfig ?? selectedPreset
  const fallbackTools = activePreset
    ? Array.from(
        new Set([
          ...(activePreset.actions ?? []),
          ...(activePreset.namespaces ?? []),
        ])
      )
    : (DEFAULT_CHAT_TOOLS[entityType] ?? [])

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
        ...(effectivePresetId ? { agent_preset_id: effectivePresetId } : {}),
        ...(effectivePresetId === null
          ? getSessionModelFields(effectiveSelectedModel)
          : {}),
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
    effectivePresetId,
    effectiveSelectedModel,
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
        ...(effectivePresetId ? { agent_preset_id: effectivePresetId } : {}),
        ...(effectivePresetId === null
          ? getSessionModelFields(effectiveSelectedModel)
          : {}),
      })
      setSelectedChatId(newChat.id)
      onChatSelect?.(newChat.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    }
  }

  const handleCreateCaseChatOnFirstSend = async (
    messageText: string,
    selectedTools?: string[]
  ) => {
    if (entityType !== "case" || createChatPending) {
      return null
    }

    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: "case",
        entity_id: entityId,
        tools: selectedTools,
        ...(effectivePresetId ? { agent_preset_id: effectivePresetId } : {}),
        ...(effectivePresetId
          ? { agent_preset_version_id: selectedPresetVersionId }
          : {}),
        ...(effectivePresetId === null
          ? getSessionModelFields(effectiveSelectedModel)
          : {}),
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

          {/* Right-side actions */}
          <div className="flex items-center gap-1">
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
          selectedPreset={activePreset}
          presetSelector={
            presetsEnabled
              ? {
                  presets,
                  presetsIsLoading,
                  presetsError,
                  selectedPresetId: effectivePresetId,
                  onSelect: handlePresetChange,
                  disabled: presetMenuDisabled,
                  showSpinner: showPresetSpinner,
                  versions,
                  versionsIsLoading,
                  versionsError,
                  selectedVersionId: selectedPresetVersionId,
                  currentVersionId: currentPresetVersionId,
                  onVersionSelect: handlePresetVersionChange,
                  versionDisabled: versionMenuDisabled,
                }
              : undefined
          }
          toolsEnabled={!activePreset}
          fallbackTools={fallbackTools}
          draftMode={
            entityType === "case" && (isCaseDraftChat || chats?.length === 0)
          }
          modelSelector={
            presetsEnabled && effectivePresetId === null
              ? {
                  label: modelSelectorLabel,
                  defaultLabel: defaultModelLabel,
                  defaultProvider: defaultModelProvider,
                  models: enabledModels,
                  modelsError: enabledModelsError,
                  modelsIsLoading: enabledModelsLoading,
                  selectedModel: effectiveSelectedModel,
                  onSelect: (model) =>
                    void handleModelSelectionChange(
                      model
                        ? {
                            source_id: model.source_id ?? null,
                            model_provider: model.model_provider,
                            model_name: model.model_name,
                          }
                        : null
                    ),
                  disabled: modelSelectorDisabled,
                  showSpinner: showModelSelectorSpinner,
                }
              : undefined
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
  selectedPreset?: PresetConfigLike
  presetSelector?: ChatPresetSelector
  toolsEnabled: boolean
  fallbackTools: string[]
  draftMode: boolean
  modelSelector?: ChatModelSelector
  onCreateSessionBeforeSend?: (
    messageText: string,
    selectedTools?: string[]
  ) => Promise<string | null>
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
  presetSelector,
  toolsEnabled,
  fallbackTools,
  draftMode,
  modelSelector,
  onCreateSessionBeforeSend,
  draftInputDisabled,
  pendingMessage,
  onPendingMessageSent,
}: ChatBodyProps) {
  const isReadonly = Boolean(chat && "is_readonly" in chat && chat.is_readonly)
  const {
    ready: chatReady,
    loading: chatReadyLoading,
    reason: chatReason,
    modelInfo,
  } = useChatReadiness(
    selectedPreset
      ? {
          workspaceId,
          selection: {
            source_id: selectedPreset.source_id ?? null,
            model_provider: selectedPreset.model_provider,
            model_name: selectedPreset.model_name,
          },
        }
      : modelSelector?.selectedModel
        ? {
            workspaceId,
            selection: {
              source_id: modelSelector.selectedModel.source_id ?? null,
              model_provider: modelSelector.selectedModel.model_provider,
              model_name: modelSelector.selectedModel.model_name,
            },
          }
        : {
            workspaceId,
          }
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
        {presetSelector && !isReadonly ? (
          <ChatPresetSelectorBar selector={presetSelector} />
        ) : null}
        {modelSelector && !isReadonly ? (
          <ChatModelSelectorBar selector={modelSelector} />
        ) : null}
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
                    (modelSelector
                      ? "Choose an enabled model for this chat or set an organization default model."
                      : "Select a default model in agent settings to enable chat.")}
                  {chatReason === "no_credentials" &&
                    `Configure ${modelInfo?.provider || "model provider"} credentials in organization agent settings to enable chat.`}
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
      <>
        {presetSelector && !isReadonly ? (
          <ChatPresetSelectorBar selector={presetSelector} />
        ) : null}
        {modelSelector && !isReadonly ? (
          <ChatModelSelectorBar selector={modelSelector} />
        ) : null}
        <ChatSessionPane
          workspaceId={workspaceId}
          entityType={entityType}
          entityId={entityId}
          placeholder={`Ask about this ${entityType}...`}
          className="flex-1 min-h-0"
          modelInfo={modelInfo}
          toolsEnabled={toolsEnabled}
          fallbackTools={fallbackTools}
          modelSelector={modelSelector}
          onBeforeSend={onCreateSessionBeforeSend}
          inputDisabled={draftInputDisabled}
          inputDisabledPlaceholder="Creating chat..."
        />
      </>
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
    <>
      {presetSelector && !isReadonly ? (
        <ChatPresetSelectorBar selector={presetSelector} />
      ) : null}
      {modelSelector && !isReadonly ? (
        <ChatModelSelectorBar selector={modelSelector} />
      ) : null}
      <ChatSessionPane
        chat={chat}
        workspaceId={workspaceId}
        entityType={entityType}
        entityId={entityId}
        placeholder={`Ask about this ${entityType}...`}
        className="flex-1 min-h-0"
        modelInfo={modelInfo}
        toolsEnabled={toolsEnabled}
        fallbackTools={fallbackTools}
        modelSelector={modelSelector}
        pendingMessage={pendingMessage ?? undefined}
        onPendingMessageSent={onPendingMessageSent}
      />
    </>
  )
}

function ChatPresetSelectorBar({ selector }: { selector: ChatPresetSelector }) {
  const noPresetValue = "__none__"
  const selectedValue = selector.selectedPresetId ?? noPresetValue

  return (
    <div className="border-b px-4 py-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1">
          <Label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Chat preset
          </Label>
          <p className="text-xs text-muted-foreground">
            Choose an agent preset for this chat or clear it to pick a model
            directly.
          </p>
        </div>
        <div className="flex w-full flex-col gap-2 md:w-auto md:flex-row md:items-center">
          <Select
            value={selectedValue}
            onValueChange={(value) =>
              void selector.onSelect(value === noPresetValue ? null : value)
            }
            disabled={selector.disabled || selector.presetsIsLoading}
          >
            <SelectTrigger className="w-full md:w-[280px]">
              <SelectValue placeholder="Choose a preset">
                {selector.showSpinner ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="size-3 animate-spin" />
                    Loading presets...
                  </span>
                ) : undefined}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={noPresetValue}>No preset</SelectItem>
              {selector.presetsIsLoading ? (
                <SelectItem value="__loading" disabled>
                  Loading presets...
                </SelectItem>
              ) : null}
              {selector.presetsError ? (
                <SelectItem value="__error" disabled>
                  Failed to load presets
                </SelectItem>
              ) : null}
              {selector.presets.map((preset) => (
                <SelectItem key={preset.id} value={preset.id}>
                  {preset.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selector.selectedPresetId ? (
            <AgentPresetVersionSelect
              versions={selector.versions}
              versionsIsLoading={selector.versionsIsLoading}
              versionsError={selector.versionsError}
              selectedVersionId={selector.selectedVersionId}
              currentVersionId={selector.currentVersionId}
              onSelect={selector.onVersionSelect}
              disabled={selector.versionDisabled}
              triggerClassName="min-w-40 border"
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}

function ChatModelSelectorBar({ selector }: { selector: ChatModelSelector }) {
  const defaultValue = "__default__"
  const selectedValue = selector.selectedModel
    ? getCompositeModelKey(selector.selectedModel)
    : defaultValue

  return (
    <div className="border-b px-4 py-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1">
          <Label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Chat model
          </Label>
          <p className="text-xs text-muted-foreground">
            Choose an enabled model for this chat or keep using the organization
            default.
          </p>
        </div>
        <div className="flex w-full flex-col gap-2 md:w-[320px]">
          <Select
            value={selectedValue}
            onValueChange={(value) => {
              if (value === defaultValue) {
                void selector.onSelect(null)
                return
              }
              const nextModel =
                selector.models?.find(
                  (model) => getCompositeModelKey(model) === value
                ) ?? null
              void selector.onSelect(nextModel)
            }}
            disabled={selector.disabled || selector.modelsIsLoading}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Choose a model" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={defaultValue}>
                Organization default · {selector.defaultLabel}
              </SelectItem>
              {selector.models?.map((model) => (
                <SelectItem
                  key={getCompositeModelKey(model)}
                  value={getCompositeModelKey(model)}
                >
                  {model.model_name} ·{" "}
                  {model.source_name ??
                    (model.source_id ? "Custom" : "Platform")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selector.showSpinner ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              Updating available models…
            </div>
          ) : null}
          {selector.modelsError ? (
            <p className="text-xs text-destructive">
              {getModelSelectorErrorMessage(selector.modelsError)}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  )
}
