"use client"

import { formatDistanceToNow } from "date-fns"
import { BoxIcon, ChevronDown, Loader2, Plus } from "lucide-react"
import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import type { AgentPresetRead, ChatRead, ChatReadVercel } from "@/client"
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
import { CopilotChatPane } from "./copilot-chat-pane"

const COPILOT_SUGGESTIONS = [
  "List open cases",
  "Show my tables",
  "Search cases by priority",
]

interface CopilotChatInterfaceProps {
  bodyClassName?: string
}

/**
 * Main workspace-level chat interface.
 *
 * Uses the workspace ID as the entity ID for workspace-level chat.
 * Supports agent presets and custom tool selection just like case chat.
 */
export function CopilotChatInterface({
  bodyClassName,
}: CopilotChatInterfaceProps) {
  const workspaceId = useWorkspaceId()
  const [selectedChatId, setSelectedChatId] = useState<string | undefined>()
  const [newChatDialogOpen, setNewChatDialogOpen] = useState(false)
  const [autoCreating, setAutoCreating] = useState(false)

  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId,
    entityType: "copilot",
    entityId: workspaceId,
  })

  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: true }
  )

  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: selectedChatId,
    workspaceId,
  })
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)

  const presetOptions = presets ?? []
  const effectivePresetId = chat?.agent_preset_id ?? null

  const { preset: selectedPreset, presetIsLoading: selectedPresetLoading } =
    useAgentPreset(workspaceId, effectivePresetId, {
      enabled: Boolean(effectivePresetId),
    })

  const handlePresetChange = async (nextPresetId: string | null) => {
    if (!selectedChatId) return
    const currentPresetId = chat?.agent_preset_id ?? null
    if (nextPresetId === currentPresetId) return

    try {
      await updateChat({
        chatId: selectedChatId,
        update: { agent_preset_id: nextPresetId },
      })
    } catch (error) {
      console.error("Failed to update chat preset:", error)
      toast({
        title: "Failed to update preset",
        description: parseChatError(error),
      })
    }
  }

  // Auto-create a chat session if none exists
  const autoCreateChat = useCallback(async () => {
    if (autoCreating || createChatPending) return
    setAutoCreating(true)
    try {
      const newChat = await createChat({
        title: "Copilot",
        entity_type: "copilot",
        entity_id: workspaceId,
      })
      setSelectedChatId(newChat.id)
    } catch (error) {
      console.error("Failed to auto-create chat:", error)
      toast({
        title: "Failed to create chat",
        description: parseChatError(error),
      })
    } finally {
      setAutoCreating(false)
    }
  }, [autoCreating, createChat, createChatPending, workspaceId])

  // Auto-select first chat or auto-create if none exists
  useEffect(() => {
    if (chatsLoading || selectedChatId) return

    if (chats && chats.length > 0) {
      setSelectedChatId(chats[0].id)
    } else if (chats && chats.length === 0 && !autoCreating) {
      autoCreateChat()
    }
  }, [chats, chatsLoading, selectedChatId, autoCreating, autoCreateChat])

  const handleCreateChat = async () => {
    setNewChatDialogOpen(false)
    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: "copilot",
        entity_id: workspaceId,
      })
      setSelectedChatId(newChat.id)
    } catch (error) {
      console.error("Failed to create chat:", error)
    }
  }

  const handleSelectChat = (chatId: string) => {
    setSelectedChatId(chatId)
  }

  const presetMenuLabel = selectedPreset?.name ?? "No preset"
  const presetMenuDisabled = !selectedChatId || chatLoading || isUpdating
  const showPresetSpinner =
    presetsIsLoading || isUpdating || chatLoading || selectedPresetLoading
  const hasPresetOptions = presetOptions.length > 0

  // Show loading while chats are being fetched or auto-created
  if (chatsLoading || (autoCreating && !selectedChatId)) {
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
          {/* Conversations dropdown */}
          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" className="px-2">
                  Conversations
                  <ChevronDown className="size-3 ml-1" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64">
                {chatsError ? (
                  <DropdownMenuItem disabled>
                    <span className="text-red-600">Failed to load chats</span>
                  </DropdownMenuItem>
                ) : (
                  <ScrollArea className="max-h-64">
                    {chats?.map((c: ChatRead) => (
                      <DropdownMenuItem
                        key={c.id}
                        onClick={() => handleSelectChat(c.id)}
                        className={cn(
                          "flex items-center justify-between cursor-pointer",
                          selectedChatId === c.id && "bg-accent"
                        )}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">
                            {c.title}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {formatDistanceToNow(new Date(c.created_at), {
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
          </div>

          {/* Right-side controls: preset selector + new chat */}
          <div className="flex items-center gap-1">
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
                    <span className="text-red-600">Failed to load presets</span>
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
                          Use workspace default agent instructions.
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

            {/* New chat button */}
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
                    This will create a new conversation. Your current chat will
                    remain accessible from the conversations menu.
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
        <CopilotChatBody
          chatId={selectedChatId}
          workspaceId={workspaceId}
          chat={chat}
          chatLoading={chatLoading}
          chatError={chatError}
          selectedPreset={selectedPreset}
          toolsEnabled={!selectedPreset}
          suggestions={COPILOT_SUGGESTIONS}
        />
      </div>
    </div>
  )
}

interface CopilotChatBodyProps {
  chatId?: string
  workspaceId: string
  chat?: ChatReadVercel
  chatLoading: boolean
  chatError: unknown
  selectedPreset?: AgentPresetRead
  toolsEnabled: boolean
  suggestions: string[]
}

function CopilotChatBody({
  chatId,
  workspaceId,
  chat,
  chatLoading,
  chatError,
  selectedPreset,
  toolsEnabled,
  suggestions,
}: CopilotChatBodyProps) {
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

  // Show configuration required state
  if (!chatReady || !modelInfo) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
        <div className="text-center">
          <h3 className="text-lg font-semibold text-foreground">
            Set up Copilot
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Configure your AI model to start using Copilot.
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link href="/organization/settings/agent">
            {chatReason === "no_model" && "Configure model"}
            {chatReason === "no_credentials" && "Add credentials"}
            <ChevronDown className="ml-1 size-4 rotate-[-90deg]" />
          </Link>
        </Button>
      </div>
    )
  }

  return (
    <CopilotChatPane
      chat={chat}
      workspaceId={workspaceId}
      placeholder="Ask Copilot anything..."
      className="flex-1 min-h-0"
      modelInfo={modelInfo}
      toolsEnabled={toolsEnabled}
      suggestions={suggestions}
    />
  )
}
