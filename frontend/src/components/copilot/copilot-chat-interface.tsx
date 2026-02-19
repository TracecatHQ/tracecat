"use client"

import { ChevronDown, Plus } from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import type {
  AgentPresetRead,
  AgentSessionsGetSessionVercelResponse,
} from "@/client"
import { AgentPresetMenu } from "@/components/chat/agent-preset-menu"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"
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

type PendingCopilotPrompt = {
  chatId: string
  text: string
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
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()

  const chatIdParam = searchParams?.get("chatId")
  const [newChatDialogOpen, setNewChatDialogOpen] = useState(false)
  const [creatingFromPrompt, setCreatingFromPrompt] = useState(false)
  const [pendingCopilotPrompt, setPendingCopilotPrompt] =
    useState<PendingCopilotPrompt | null>(null)

  const { chats, chatsLoading, chatsError } = useListChats({
    workspaceId,
    entityType: "copilot",
    entityId: workspaceId,
  })

  const selectedChatId = chatIdParam ?? undefined

  const { createChat, createChatPending } = useCreateChat(workspaceId)

  const { chat, chatLoading, chatError } = useGetChatVercel({
    chatId: selectedChatId,
    workspaceId,
  })
  const { updateChat, isUpdating } = useUpdateChat(workspaceId)

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
    enabled: true,
  })

  const handleCreateChat = () => {
    setNewChatDialogOpen(false)
    setPendingCopilotPrompt(null)
    router.push(pathname)
  }

  const handleStartChatFromPrompt = async (promptText: string) => {
    if (creatingFromPrompt || createChatPending) {
      return false
    }

    const trimmedPrompt = promptText.trim()
    if (!trimmedPrompt) {
      return false
    }

    setCreatingFromPrompt(true)
    try {
      const newChat = await createChat({
        title: `Chat ${(chats?.length || 0) + 1}`,
        entity_type: "copilot",
        entity_id: workspaceId,
        agent_preset_id: effectivePresetId,
      })
      setPendingCopilotPrompt({ chatId: newChat.id, text: trimmedPrompt })
      router.push(`${pathname}?chatId=${newChat.id}`)
      return true
    } catch (error) {
      console.error("Failed to create chat from prompt:", error)
      toast({
        title: "Failed to create chat",
        description: parseChatError(error),
        variant: "destructive",
      })
      return false
    } finally {
      setCreatingFromPrompt(false)
    }
  }

  const handleSelectChat = (chatId: string) => {
    router.push(`${pathname}?chatId=${chatId}`)
  }

  if (chatsLoading) {
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
          {/* Chats dropdown */}
          <div className="flex items-center gap-2">
            <ChatHistoryDropdown
              chats={chats}
              isLoading={chatsLoading}
              error={chatsError}
              selectedChatId={selectedChatId}
              onSelectChat={handleSelectChat}
            />
          </div>

          {/* Right-side controls: preset selector + new chat */}
          <div className="flex items-center gap-1">
            <AgentPresetMenu
              label={presetMenuLabel}
              presets={presetOptions}
              presetsIsLoading={presetsIsLoading}
              presetsError={presetsError}
              selectedPresetId={effectivePresetId}
              disabled={presetMenuDisabled}
              showSpinner={showPresetSpinner}
              onSelect={(presetId) => void handlePresetChange(presetId)}
            />

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
                        disabled={createChatPending || creatingFromPrompt}
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
                    This opens a fresh Copilot draft. A new conversation will be
                    created after you send your first message.
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
          onStartChatFromPrompt={handleStartChatFromPrompt}
          startingChatFromPrompt={creatingFromPrompt}
          pendingPromptText={
            selectedChatId && pendingCopilotPrompt?.chatId === selectedChatId
              ? pendingCopilotPrompt.text
              : null
          }
          onPendingPromptSent={() =>
            setPendingCopilotPrompt((current) =>
              current?.chatId === selectedChatId ? null : current
            )
          }
        />
      </div>
    </div>
  )
}

interface CopilotChatBodyProps {
  chatId?: string
  workspaceId: string
  chat?: AgentSessionsGetSessionVercelResponse
  chatLoading: boolean
  chatError: unknown
  selectedPreset?: AgentPresetRead
  toolsEnabled: boolean
  suggestions: string[]
  onStartChatFromPrompt: (promptText: string) => Promise<boolean>
  startingChatFromPrompt: boolean
  pendingPromptText: string | null
  onPendingPromptSent: () => void
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
  onStartChatFromPrompt,
  startingChatFromPrompt,
  pendingPromptText,
  onPendingPromptSent,
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

  if (!chatId) {
    return (
      <CopilotChatPane
        workspaceId={workspaceId}
        placeholder="Ask Copilot anything..."
        className="flex-1 min-h-0"
        modelInfo={modelInfo}
        toolsEnabled={false}
        suggestions={suggestions}
        onBeforeSend={async (message) =>
          onStartChatFromPrompt(message.text ?? "")
        }
        inputDisabled={startingChatFromPrompt}
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
    <CopilotChatPane
      chat={chat}
      workspaceId={workspaceId}
      placeholder="Ask Copilot anything..."
      className="flex-1 min-h-0"
      modelInfo={modelInfo}
      toolsEnabled={toolsEnabled}
      suggestions={suggestions}
      pendingMessage={pendingPromptText}
      onPendingMessageSent={onPendingPromptSent}
    />
  )
}
