"use client"

import { formatDistanceToNow } from "date-fns"
import {
  BoxIcon,
  ChevronDown,
  Loader2,
  MessageSquare,
  Plus,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type {
  AgentPresetRead,
  ChatEntity,
  ChatRead,
  ChatReadVercel,
} from "@/client"
import { ChatSessionPane } from "@/components/chat/chat-session-pane"
import { NoMessages } from "@/components/chat/messages"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useAgentPreset, useAgentPresets } from "@/hooks/use-agent-presets"
import {
  parseChatError,
  useCreateChat,
  useGetChatVercel,
  useListChats,
  useUpdateChat,
} from "@/hooks/use-chat"
import { useChatReadiness } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface ChatInterfaceProps {
  chatId?: string
  entityType: ChatEntity
  entityId: string
  onChatSelect?: (chatId: string) => void
}

export function ChatInterface({
  chatId,
  entityType,
  entityId,
  onChatSelect,
}: ChatInterfaceProps) {
  const workspaceId = useWorkspaceId()
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>(
    chatId
  )

  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId: workspaceId,
    entityType,
    entityId,
  })

  // Create chat mutation
  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const presetsEnabled = entityType === "case" || entityType === "workspace"
  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: presetsEnabled }
  )

  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: selectedChatId,
    workspaceId,
  })
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)

  const presetOptions = presetsEnabled ? (presets ?? []) : []
  const effectivePresetId = chat?.agent_preset_id ?? null

  // Fetch full preset data when a preset is selected
  const { preset: selectedPreset, presetIsLoading: selectedPresetLoading } =
    useAgentPreset(workspaceId, effectivePresetId, {
      enabled: presetsEnabled && Boolean(effectivePresetId),
    })

  const handlePresetChange = async (nextPresetId: string | null) => {
    if (!selectedChatId) {
      return
    }
    const currentPresetId = chat?.agent_preset_id ?? null
    if (nextPresetId === currentPresetId) {
      return
    }

    try {
      await updateChat({
        chatId: selectedChatId,
        update: {
          agent_preset_id: nextPresetId,
        },
      })
    } catch (error) {
      console.error("Failed to update chat preset:", error)
      toast({
        title: "Failed to update preset",
        description: parseChatError(error),
        variant: "destructive",
      })
    }
  }

  // Set the first chat as selected when chats are loaded and no chat is selected
  useEffect(() => {
    if (chats && chats.length > 0 && !selectedChatId) {
      const firstChatId = chats[0].id
      setSelectedChatId(firstChatId)
      onChatSelect?.(firstChatId)
    }
  }, [chats, selectedChatId, onChatSelect])

  const handleCreateChat = async () => {
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

  const handleSelectChat = (chatId: string) => {
    setSelectedChatId(chatId)
    onChatSelect?.(chatId)
  }

  const presetMenuLabel = selectedPreset?.name ?? "No preset"
  const presetMenuDisabled =
    !presetsEnabled || !selectedChatId || chatLoading || isUpdating
  const showPresetSpinner =
    presetsIsLoading || isUpdating || chatLoading || selectedPresetLoading
  const hasPresetOptions = presetOptions.length > 0

  // Show empty state if no chats exist
  if (chats && chats.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {/* Empty State */}
        <div className="flex h-full items-center justify-center p-8">
          <div className="text-center max-w-sm">
            <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
            <h4 className="text-sm font-medium text-gray-900 mb-1">
              No chat sessions
            </h4>
            <p className="text-xs text-gray-500 mb-4">
              Create a chat session to start conversing with the AI assistant
              about this {entityType}.
            </p>
            <Button onClick={handleCreateChat} disabled={createChatPending}>
              <Plus className="h-3 w-3 mr-1" />
              Create chat session
            </Button>
          </div>
        </div>
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
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" className="px-2">
                  Conversations
                  <ChevronDown className="size-3 ml-1" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64">
                {chatsLoading ? (
                  <div className="p-2">
                    <div className="space-y-2">
                      <div className="h-8 bg-muted animate-pulse rounded" />
                      <div className="h-8 bg-muted animate-pulse rounded" />
                    </div>
                  </div>
                ) : chatsError ? (
                  <DropdownMenuItem disabled>
                    <span className="text-red-600">Failed to load chats</span>
                  </DropdownMenuItem>
                ) : (
                  <ScrollArea className="max-h-64">
                    {chats?.map((chat: ChatRead) => (
                      <DropdownMenuItem
                        key={chat.id}
                        onClick={() => handleSelectChat(chat.id)}
                        className={cn(
                          "flex items-center justify-between cursor-pointer",
                          selectedChatId === chat.id && "bg-accent"
                        )}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">
                            {chat.title}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {formatDistanceToNow(new Date(chat.created_at), {
                              addSuffix: true,
                            })}
                          </div>
                        </div>
                      </DropdownMenuItem>
                    ))}
                  </ScrollArea>
                )}
              </DropdownMenuContent>
            </DropdownMenu>

            {/* (left-side plus removed) */}
          </div>

          {/* Right-side controls: preset selector + actions */}
          <div className="flex items-center gap-1">
            {presetsEnabled && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="px-2"
                    disabled={presetMenuDisabled}
                  >
                    <div className="flex items-center gap-1.5">
                      <BoxIcon className="size-3 text-muted-foreground" />
                      <span
                        className="max-w-[11rem] truncate"
                        title={presetMenuLabel}
                      >
                        {presetMenuLabel}
                      </span>
                    </div>
                    {showPresetSpinner ? (
                      <Loader2 className="ml-1 size-3 animate-spin text-muted-foreground" />
                    ) : (
                      <ChevronDown className="ml-1 size-3" />
                    )}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-64">
                  {presetsIsLoading ? (
                    <div className="flex items-center gap-2 p-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Loading presets…
                    </div>
                  ) : presetsError ? (
                    <DropdownMenuItem disabled>
                      <span className="text-red-600">
                        Failed to load presets
                      </span>
                    </DropdownMenuItem>
                  ) : (
                    <ScrollArea className="max-h-64">
                      <div className="py-1">
                        <DropdownMenuItem
                          onClick={() => handlePresetChange(null)}
                          className={cn(
                            "flex cursor-pointer flex-col items-start gap-1 py-2",
                            effectivePresetId === null && "bg-accent"
                          )}
                        >
                          <span className="text-sm font-medium">No preset</span>
                          <span className="text-xs text-muted-foreground">
                            Use workspace default case agent instructions.
                          </span>
                        </DropdownMenuItem>
                        {hasPresetOptions ? (
                          presetOptions.map((preset) => (
                            <DropdownMenuItem
                              key={preset.id}
                              onClick={() => handlePresetChange(preset.id)}
                              className={cn(
                                "flex cursor-pointer flex-col items-start gap-1 py-2",
                                effectivePresetId === preset.id && "bg-accent"
                              )}
                            >
                              <span className="text-sm font-medium">
                                {preset.name}
                              </span>
                              {preset.description && (
                                <span className="text-xs text-muted-foreground">
                                  {preset.description}
                                </span>
                              )}
                            </DropdownMenuItem>
                          ))
                        ) : (
                          <DropdownMenuItem disabled className="py-2">
                            <span className="text-xs text-muted-foreground">
                              No presets available
                            </span>
                          </DropdownMenuItem>
                        )}
                      </div>
                    </ScrollArea>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {/* New chat icon button with tooltip */}
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="size-6 p-0"
                    onClick={handleCreateChat}
                    disabled={createChatPending}
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">New chat</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>
      </div>

      {/* Chat Body */}
      <div className="flex flex-1 min-h-0 flex-col">
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
        />
      </div>
    </div>
  )
}

interface ChatBodyProps {
  chatId?: string
  workspaceId: string
  entityType: ChatEntity
  entityId: string
  chat?: ChatReadVercel
  chatLoading: boolean
  chatError: unknown
  selectedPreset?: AgentPresetRead
  toolsEnabled: boolean
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

  if (!chatId || chatLoading || chatReadyLoading || !chat) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }
  if (chatError) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-red-500">Failed to load chat</span>
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
    />
  )
}
