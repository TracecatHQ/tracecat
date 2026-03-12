"use client"

import { useQueryClient } from "@tanstack/react-query"
import {
  type ChatStatus,
  getToolName,
  isToolUIPart,
  type UIDataTypes,
  type UIMessage,
  type UIMessagePart,
  type UITools,
} from "ai"
import {
  CheckIcon,
  Loader2,
  PencilIcon,
  RefreshCcwIcon,
  XIcon,
} from "lucide-react"
import { motion } from "motion/react"
import {
  type ChangeEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type {
  AgentSessionEntity,
  AgentSessionReadVercel,
  ApprovalDecision,
  ChatReadVercel,
} from "@/client"
import { Action, Actions } from "@/components/ai-elements/actions"
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import { Message, MessageContent } from "@/components/ai-elements/message"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  type PromptInputMessage,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input"
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning"
import { Response } from "@/components/ai-elements/response"
import {
  Source,
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources"
import {
  getStatusBadge,
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getIcon, ProviderIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Dots } from "@/components/loading/dots"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import {
  type ApprovalCard,
  makeContinueMessage,
  parseChatError,
  useUpdateChat,
  useVercelChat,
} from "@/hooks/use-chat"
import type { ModelInfo } from "@/lib/chat"
import {
  ENTITY_TO_INVALIDATION,
  toUIMessage,
  transformMessages,
} from "@/lib/chat"
import type { AgentCatalogEntry } from "@/lib/hooks"
import { getModelSelectionKey, useBuilderRegistryActions } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const MAX_TOOL_MENTION_RESULTS = 40
const MAX_VISIBLE_SELECTED_TOOLS = 3

type ToolMentionToken = {
  start: number
  end: number
  query: string
}

type ToolMentionState = ToolMentionToken & {
  activeIndex: number
}

type ToolSuggestion = {
  value: string
  label: string
  description?: string
  group?: string
}

function areToolListsEqual(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((tool, index) => tool === right[index])
  )
}

function getToolMentionToken(
  text: string,
  caret: number
): ToolMentionToken | undefined {
  const beforeCaret = text.slice(0, caret)
  const atIndex = beforeCaret.lastIndexOf("@")
  if (atIndex < 0) {
    return undefined
  }

  const priorChar = atIndex === 0 ? " " : beforeCaret[atIndex - 1]
  if (priorChar.trim() !== "") {
    return undefined
  }

  const query = beforeCaret.slice(atIndex + 1)
  if (/\s/.test(query)) {
    return undefined
  }

  return {
    start: atIndex,
    end: caret,
    query,
  }
}

type ChatFooterModelSelector = {
  label: string
  defaultLabel: string
  defaultProvider?: string | null
  models?: AgentCatalogEntry[]
  modelsIsLoading: boolean
  modelsError: unknown
  selectedModel: AgentCatalogEntry | null
  onSelect: (model: AgentCatalogEntry | null) => void | Promise<void>
  disabled?: boolean
  showSpinner?: boolean
}

function getCompositeModelKey(
  model: Pick<AgentCatalogEntry, "source_id" | "model_provider" | "model_name">
): string {
  return getModelSelectionKey(model)
}

export interface ChatSessionPaneProps {
  chat?: AgentSessionReadVercel | ChatReadVercel
  workspaceId: string
  entityType?: AgentSessionEntity
  entityId?: string
  className?: string
  placeholder?: string
  onMessagesChange?: (messages: UIMessage[]) => void
  modelInfo: ModelInfo
  toolsEnabled?: boolean
  fallbackTools?: string[]
  /** Autofocus the prompt input when the pane mounts. */
  autoFocusInput?: boolean
  /**
   * Called before sending a message. If provided, receives the message text
   * and should handle sending it (e.g., to a forked session). Returns the
   * new session ID to switch to, or null to cancel.
   * Used for inbox fork-on-send behavior.
   */
  onBeforeSend?: (
    messageText: string,
    selectedTools?: string[]
  ) => Promise<string | null>
  /**
   * Message to send immediately on mount. Used after forking a session
   * to send the user's message to the newly forked session.
   */
  pendingMessage?: string
  /**
   * Callback when the pending message has been sent.
   * Used to clear the pending message state in the parent.
   */
  onPendingMessageSent?: () => void
  /**
   * Disable the input field. Used when user must take an action
   * (e.g., make an approval decision) before sending messages.
   */
  inputDisabled?: boolean
  /**
   * Placeholder to show when input is disabled.
   */
  inputDisabledPlaceholder?: string
  modelSelector?: ChatFooterModelSelector
}

export function ChatSessionPane({
  chat,
  workspaceId,
  entityType,
  entityId,
  className,
  placeholder = "Ask your question...",
  onMessagesChange,
  modelInfo,
  toolsEnabled = true,
  fallbackTools,
  autoFocusInput = false,
  onBeforeSend,
  pendingMessage,
  onPendingMessageSent,
  inputDisabled = false,
  inputDisabledPlaceholder,
  modelSelector,
}: ChatSessionPaneProps) {
  const queryClient = useQueryClient()
  const processedMessageRef = useRef<
    | {
        messageId: string
        partCount: number
      }
    | undefined
  >(undefined)

  const [input, setInput] = useState<string>("")
  const [toolMention, setToolMention] = useState<ToolMentionState>()
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const { updateChat, isUpdating: isUpdatingTools } = useUpdateChat(workspaceId)
  const { registryActions, registryActionsIsLoading } =
    useBuilderRegistryActions()

  // Check if this is a legacy read-only session
  const isReadonly = chat ? "is_readonly" in chat && chat.is_readonly : false

  const uiMessages = useMemo(
    () => (chat?.messages || []).map(toUIMessage),
    [chat?.messages]
  )
  const { sendMessage, messages, status, regenerate, lastError, clearError } =
    useVercelChat({
      chatId: chat?.id,
      workspaceId,
      messages: uiMessages,
      modelInfo,
    })

  // Track pending message sends to avoid duplicate sends
  const pendingMessageSentRef = useRef<string | null>(null)

  // Send pending message on mount (used after forking)
  useEffect(() => {
    if (!pendingMessage || isReadonly || !chat) return

    const messageKey = `${chat.id}:${pendingMessage}`
    if (pendingMessageSentRef.current === messageKey) return

    pendingMessageSentRef.current = messageKey
    clearError()
    sendMessage({ text: pendingMessage })
    onPendingMessageSent?.()
  }, [
    pendingMessage,
    isReadonly,
    chat,
    clearError,
    sendMessage,
    onPendingMessageSent,
  ])

  // Allow input when ready OR in error state (so user can retry after transient failures)
  const canSubmit = status === "ready" || status === "error"

  const isWaitingForResponse = useMemo(() => {
    if (status === "submitted") return true
    if (status === "streaming") {
      const lastMessage = messages[messages.length - 1]
      if (!lastMessage || lastMessage.role !== "assistant") return true

      const lastPart = lastMessage.parts[lastMessage.parts.length - 1]
      if (!lastPart) return true

      const isStreamingVisual =
        (lastPart.type === "text" && lastPart.text.length > 0) ||
        (lastPart.type === "reasoning" && lastPart.text.length > 0) ||
        (isToolUIPart(lastPart) && lastPart.state === "input-streaming")

      if (lastPart.type === "data-approval-request") return false

      return !isStreamingVisual
    }
    return false
  }, [status, messages])

  const handleSubmitApprovals = useCallback(
    async (decisionPayload: ApprovalDecision[]) => {
      if (!decisionPayload.length) return
      try {
        clearError()
        await sendMessage(makeContinueMessage(decisionPayload))
      } catch (error) {
        console.error("Failed to submit approvals", error)
        toast({
          variant: "destructive",
          title: "Approval submission failed",
          description:
            error instanceof Error ? error.message : "Please try again.",
        })
        throw error
      }
    },
    [clearError, sendMessage]
  )

  useEffect(() => {
    onMessagesChange?.(messages)
  }, [messages, onMessagesChange])

  const toolSuggestions = useMemo<ToolSuggestion[]>(() => {
    const actions = registryActions ?? []
    return actions
      .map((action) => ({
        value: action.action,
        label: action.default_title || action.action,
        description: action.description ?? undefined,
        group: action.namespace,
      }))
      .sort((left, right) => left.value.localeCompare(right.value))
  }, [registryActions])

  const toolSuggestionMap = useMemo(
    () => new Map(toolSuggestions.map((tool) => [tool.value, tool])),
    [toolSuggestions]
  )

  const persistToolsChainRef = useRef<Promise<void>>(Promise.resolve())
  const selectedToolsRef = useRef<string[]>([])
  const pendingPersistedToolsRef = useRef<string[] | null>(null)
  const syncedChatIdRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    selectedToolsRef.current = selectedTools
  }, [selectedTools])

  useEffect(() => {
    const nextChatId = chat?.id
    const nextTools = nextChatId
      ? (chat?.tools ?? [])
      : toolsEnabled
        ? (fallbackTools ?? [])
        : []

    if (syncedChatIdRef.current !== nextChatId) {
      syncedChatIdRef.current = nextChatId
      pendingPersistedToolsRef.current = null
      selectedToolsRef.current = nextTools
      setSelectedTools(nextTools)
      return
    }

    const pendingTools = pendingPersistedToolsRef.current
    if (pendingTools) {
      if (areToolListsEqual(nextTools, pendingTools)) {
        pendingPersistedToolsRef.current = null
      } else {
        // Keep the optimistic selection visible until the invalidated chat query
        // catches up, otherwise intermediate server echoes can flicker chips away.
        return
      }
    }

    if (!areToolListsEqual(selectedToolsRef.current, nextTools)) {
      selectedToolsRef.current = nextTools
      setSelectedTools(nextTools)
    }
  }, [chat?.id, chat?.tools, fallbackTools, toolsEnabled])

  const queuePersistTools = useCallback(
    (tools: string[]) => {
      if (!chat || isReadonly) {
        return
      }

      const chatId = chat.id
      persistToolsChainRef.current = persistToolsChainRef.current
        .catch(() => undefined)
        .then(async () => {
          try {
            await updateChat({
              chatId,
              update: { tools },
            })
          } catch (error) {
            if (
              pendingPersistedToolsRef.current &&
              areToolListsEqual(pendingPersistedToolsRef.current, tools)
            ) {
              pendingPersistedToolsRef.current = null
            }
            toast({
              title: "Failed to update tools",
              description: parseChatError(error),
              variant: "destructive",
            })
          }
        })
    },
    [chat, isReadonly, updateChat]
  )

  const commitSelectedTools = useCallback(
    (next: string[]) => {
      if (areToolListsEqual(selectedToolsRef.current, next)) {
        return
      }

      selectedToolsRef.current = next
      setSelectedTools(next)

      if (!chat || isReadonly) {
        pendingPersistedToolsRef.current = null
        return
      }

      pendingPersistedToolsRef.current = next
      void queuePersistTools(next)
    },
    [chat, isReadonly, queuePersistTools]
  )

  const addSelectedTool = useCallback(
    (toolName: string) => {
      if (selectedToolsRef.current.includes(toolName)) {
        return
      }

      commitSelectedTools([...selectedToolsRef.current, toolName])
    },
    [commitSelectedTools]
  )

  const removeSelectedTool = useCallback(
    (toolName: string) => {
      commitSelectedTools(
        selectedToolsRef.current.filter((tool) => tool !== toolName)
      )
    },
    [commitSelectedTools]
  )

  const mentionEnabled =
    toolsEnabled && !isReadonly && !inputDisabled && canSubmit

  useEffect(() => {
    if (!mentionEnabled) {
      setToolMention(undefined)
    }
  }, [mentionEnabled])

  const filteredToolSuggestions = useMemo(() => {
    if (!toolMention) {
      return []
    }

    const needle = toolMention.query.trim().toLowerCase()
    const matches = toolSuggestions.filter((tool) => {
      if (!needle) {
        return true
      }
      return [tool.value, tool.label, tool.description ?? "", tool.group ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(needle)
    })
    return matches.slice(0, MAX_TOOL_MENTION_RESULTS)
  }, [toolMention, toolSuggestions])

  useEffect(() => {
    if (!toolMention) {
      return
    }
    if (filteredToolSuggestions.length === 0) {
      setToolMention((current) => {
        if (!current || current.activeIndex === 0) {
          return current
        }
        return { ...current, activeIndex: 0 }
      })
      return
    }

    setToolMention((current) => {
      if (!current) {
        return current
      }
      const clampedIndex = Math.min(
        current.activeIndex,
        filteredToolSuggestions.length - 1
      )
      if (clampedIndex === current.activeIndex) {
        return current
      }
      return { ...current, activeIndex: clampedIndex }
    })
  }, [filteredToolSuggestions.length, toolMention])

  const handleSelectMentionTool = useCallback(
    (toolName: string, textarea?: HTMLTextAreaElement) => {
      const mention = toolMention
      if (!mention) {
        return
      }

      addSelectedTool(toolName)
      setInput((current) => {
        const before = current.slice(0, mention.start)
        const after = current.slice(mention.end)
        return `${before}${after}`
      })
      setToolMention(undefined)

      if (!textarea) {
        return
      }

      const caretPosition = mention.start
      requestAnimationFrame(() => {
        textarea.focus()
        textarea.setSelectionRange(caretPosition, caretPosition)
      })
    },
    [addSelectedTool, toolMention]
  )

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const nextText = event.target.value
      setInput(nextText)

      if (!mentionEnabled) {
        setToolMention(undefined)
        return
      }

      const caret = event.target.selectionStart ?? nextText.length
      const nextMention = getToolMentionToken(nextText, caret)
      if (!nextMention) {
        setToolMention(undefined)
        return
      }

      setToolMention((current) => ({
        ...nextMention,
        activeIndex: current?.activeIndex ?? 0,
      }))
    },
    [mentionEnabled]
  )

  const handleInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!toolMention) {
        return
      }

      if (event.key === "Escape") {
        event.preventDefault()
        setToolMention(undefined)
        return
      }

      if (event.key === "ArrowDown") {
        event.preventDefault()
        if (filteredToolSuggestions.length === 0) {
          return
        }
        setToolMention((current) => {
          if (!current) {
            return current
          }
          return {
            ...current,
            activeIndex:
              (current.activeIndex + 1) % filteredToolSuggestions.length,
          }
        })
        return
      }

      if (event.key === "ArrowUp") {
        event.preventDefault()
        if (filteredToolSuggestions.length === 0) {
          return
        }
        setToolMention((current) => {
          if (!current) {
            return current
          }
          const nextIndex =
            (current.activeIndex - 1 + filteredToolSuggestions.length) %
            filteredToolSuggestions.length
          return { ...current, activeIndex: nextIndex }
        })
        return
      }

      if (
        (event.key === "Enter" || event.key === "Tab") &&
        filteredToolSuggestions.length > 0
      ) {
        event.preventDefault()
        const selected =
          filteredToolSuggestions[toolMention.activeIndex] ??
          filteredToolSuggestions[0]
        if (selected) {
          handleSelectMentionTool(selected.value, event.currentTarget)
        }
      }
    },
    [filteredToolSuggestions, handleSelectMentionTool, toolMention]
  )

  const displayedToolIds = !toolsEnabled ? (fallbackTools ?? []) : selectedTools

  const selectedToolBadges = useMemo(
    () =>
      displayedToolIds.map((toolName) => {
        const suggestion = toolSuggestionMap.get(toolName)
        return {
          value: toolName,
          label: suggestion?.label ?? toolName,
          icon: getIcon(toolName, {
            className: "size-5 shrink-0",
          }),
        }
      }),
    [displayedToolIds, toolSuggestionMap]
  )
  const visibleSelectedToolBadges = useMemo(
    () => selectedToolBadges.slice(0, MAX_VISIBLE_SELECTED_TOOLS),
    [selectedToolBadges]
  )
  const hiddenSelectedToolBadges = useMemo(
    () => selectedToolBadges.slice(MAX_VISIBLE_SELECTED_TOOLS),
    [selectedToolBadges]
  )

  const transformedMessages = useMemo(
    () => transformMessages(messages),
    [messages]
  )

  const invalidateEntityQueries = useCallback(
    (toolNames: string[]) => {
      if (!entityType || !entityId) {
        return
      }

      const invalidation = ENTITY_TO_INVALIDATION[entityType]
      if (!invalidation) {
        return
      }

      const { predicate, handler } = invalidation
      if (toolNames.some(predicate)) {
        handler(queryClient, workspaceId, entityId)
      }
    },
    [entityId, entityType, queryClient, workspaceId]
  )

  useEffect(() => {
    if (messages.length === 0) {
      return
    }

    const lastMessage = messages[messages.length - 1]
    const currentPartCount = lastMessage.parts?.length || 0

    // Skip if we've already processed this message with the same number of parts
    if (
      processedMessageRef.current?.messageId === lastMessage.id &&
      processedMessageRef.current?.partCount === currentPartCount
    ) {
      return
    }

    const toolNames = lastMessage.parts.filter(isToolUIPart).map(getToolName)

    if (toolNames.length > 0) {
      invalidateEntityQueries(toolNames)
    }

    processedMessageRef.current = {
      messageId: lastMessage.id,
      partCount: currentPartCount,
    }
  }, [messages, invalidateEntityQueries])

  const handleSubmit = async (message: PromptInputMessage) => {
    const hasText = Boolean(message.text?.trim())

    if (!hasText) {
      return
    }

    const messageText = message.text || ""

    if (onBeforeSend) {
      const result = await onBeforeSend(messageText, selectedTools)
      // Only clear input if onBeforeSend succeeded (non-null)
      // If null, the action was cancelled and user keeps their draft
      if (result !== null) {
        setInput("")
      }
      // Parent will handle switching sessions and sending via pendingMessage
      return
    }

    if (!chat) {
      return
    }

    // Clear input for normal message sending
    setInput("")

    try {
      await persistToolsChainRef.current.catch(() => undefined)
      clearError()
      sendMessage({
        text: messageText,
        ...(message.files?.length ? { files: message.files } : {}),
      })
    } catch (error) {
      console.error("Failed to send message:", error)
    }
  }

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex flex-1 min-h-0 flex-col">
        <Conversation className="flex-1">
          <ConversationContent>
            {lastError && (
              <Alert variant="destructive" className="mb-4">
                <AlertTitle>Unable to continue with this model</AlertTitle>
                <AlertDescription>{lastError}</AlertDescription>
              </Alert>
            )}
            {transformedMessages.map(({ id, role, parts }) => {
              // Track whether this message is the latest entry so we can keep its actions visible.
              const isLastMessage = id === messages[messages.length - 1].id
              return (
                <div key={id} className="group relative">
                  {role === "assistant" &&
                    parts &&
                    parts.filter((part) => part.type === "source-url").length >
                      0 && (
                      <Sources>
                        <SourcesTrigger
                          count={
                            parts.filter((part) => part.type === "source-url")
                              .length
                          }
                        />
                        {parts
                          .filter((part) => part.type === "source-url")
                          .map((part, partIdx) => (
                            <SourcesContent
                              key={`${id}-${part.type}-${partIdx}`}
                            >
                              <Source
                                href={"url" in part ? part.url : "#"}
                                title={"url" in part ? part.url : "Source"}
                              />
                            </SourcesContent>
                          ))}
                      </Sources>
                    )}

                  {parts?.map((part, partIdx) => (
                    <MessagePart
                      key={`${id}-${part.type}-${partIdx}`}
                      part={part}
                      partIdx={partIdx}
                      id={id}
                      role={role}
                      status={status}
                      isLastMessage={isLastMessage}
                      onSubmitApprovals={handleSubmitApprovals}
                    />
                  ))}
                  {role === "assistant" &&
                    parts &&
                    parts.length > 0 &&
                    !isWaitingForResponse && (
                      // Render response actions for assistant messages and reveal them on hover for older messages.
                      <Actions
                        className={cn(
                          "mt-4",
                          // Apply a smooth transition so the actions fade in and out gracefully.
                          "transition-opacity duration-200 ease-out",
                          // Hide actions by default for non-last messages and reveal them when the message group is hovered.
                          !isLastMessage &&
                            "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100"
                        )}
                      >
                        {isLastMessage && (
                          <Action
                            size="sm"
                            onClick={() => regenerate()}
                            label="Retry"
                            tooltip="Retry"
                          >
                            <RefreshCcwIcon className="size-3" />
                          </Action>
                        )}
                      </Actions>
                    )}
                </div>
              )
            })}
            {isWaitingForResponse && (
              <motion.div
                className="mt-5"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3, ease: "easeInOut" }}
              >
                <Dots />
              </motion.div>
            )}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      </div>
      <div className="relative px-3 pb-3">
        {mentionEnabled && toolMention && (
          <div className="absolute inset-x-3 bottom-full z-30 mb-2">
            <div className="overflow-hidden rounded-md border bg-popover shadow-md">
              {registryActionsIsLoading ? (
                <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" />
                  Loading tools...
                </div>
              ) : null}
              {!registryActionsIsLoading &&
                filteredToolSuggestions.length === 0 && (
                  <div className="px-3 py-2 text-xs text-muted-foreground">
                    No tools found for
                    {` "${toolMention.query}"`}.
                  </div>
                )}
              {!registryActionsIsLoading &&
                filteredToolSuggestions.length > 0 && (
                  <div className="max-h-64 overflow-y-auto p-1">
                    {filteredToolSuggestions.map((tool, index) => {
                      const isActive = toolMention.activeIndex === index
                      const isSelected = selectedTools.includes(tool.value)

                      return (
                        <button
                          key={tool.value}
                          type="button"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => handleSelectMentionTool(tool.value)}
                          className={cn(
                            "flex w-full items-start justify-between gap-2 rounded-sm px-2 py-2 text-left",
                            isActive && "bg-accent"
                          )}
                        >
                          <div className="flex min-w-0 items-start gap-2">
                            {getIcon(tool.value, {
                              className: "mt-0.5 size-6 shrink-0",
                            })}
                            <div className="min-w-0">
                              <p className="truncate text-xs font-medium text-foreground">
                                {tool.label}
                              </p>
                              <p className="truncate text-[11px] text-muted-foreground">
                                {tool.value}
                              </p>
                            </div>
                          </div>
                          {isSelected ? (
                            <CheckIcon className="mt-0.5 size-3.5 text-muted-foreground" />
                          ) : null}
                        </button>
                      )
                    })}
                  </div>
                )}
            </div>
          </div>
        )}
        <PromptInput onSubmit={handleSubmit}>
          {toolsEnabled && selectedToolBadges.length > 0 && (
            <PromptInputHeader className="gap-1.5 px-3 pt-3">
              {visibleSelectedToolBadges.map((tool) => (
                <Badge
                  key={tool.value}
                  variant="secondary"
                  className="h-7 max-w-[11rem] gap-1.5 px-2.5 text-xs"
                >
                  <span className="inline-flex items-center justify-center text-foreground">
                    {tool.icon}
                  </span>
                  <span className="truncate">{tool.label}</span>
                  <button
                    type="button"
                    className="inline-flex items-center text-muted-foreground hover:text-foreground"
                    aria-label={`Remove ${tool.label}`}
                    onClick={() => removeSelectedTool(tool.value)}
                    disabled={
                      isUpdatingTools ||
                      isReadonly ||
                      inputDisabled ||
                      !toolsEnabled
                    }
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </Badge>
              ))}
              {hiddenSelectedToolBadges.length > 0 ? (
                <SelectedToolsOverflow
                  disabled={
                    isUpdatingTools ||
                    isReadonly ||
                    inputDisabled ||
                    !toolsEnabled
                  }
                  onClearAll={() => commitSelectedTools([])}
                  onRemove={removeSelectedTool}
                  tools={selectedToolBadges}
                />
              ) : null}
            </PromptInputHeader>
          )}
          <PromptInputBody>
            <PromptInputTextarea
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onBlur={() => setToolMention(undefined)}
              placeholder={
                isReadonly
                  ? "This is a legacy session (read-only)"
                  : inputDisabled && inputDisabledPlaceholder
                    ? inputDisabledPlaceholder
                    : placeholder
              }
              value={input}
              autoFocus={autoFocusInput && !isReadonly && !inputDisabled}
              disabled={isReadonly || inputDisabled || !canSubmit}
            />
          </PromptInputBody>
          <PromptInputFooter>
            <PromptInputTools>
              {!isReadonly && modelSelector ? (
                <PromptModelSelector
                  selector={modelSelector}
                  disabled={inputDisabled || !canSubmit}
                />
              ) : !isReadonly ? (
                <PromptModelIndicator modelInfo={modelInfo} />
              ) : null}
            </PromptInputTools>
            <PromptInputSubmit
              disabled={
                isReadonly || inputDisabled || !canSubmit || !input.trim()
              }
              status={status}
              className="text-muted-foreground/80"
            />
          </PromptInputFooter>
        </PromptInput>
      </div>
    </div>
  )
}

function PromptModelSelector({
  selector,
  disabled = false,
}: {
  selector: ChatFooterModelSelector
  disabled?: boolean
}) {
  const effectiveDisabled = Boolean(disabled || selector.disabled)
  const defaultValue = "__organization_default_model__"
  const selectedModel = selector.selectedModel
  const triggerLabel =
    selectedModel?.model_name ??
    (selector.selectedModel === null
      ? `${selector.defaultLabel} (default)`
      : selector.label)
  const triggerProvider =
    selectedModel?.model_provider ?? selector.defaultProvider ?? null

  return (
    <Select
      value={
        selector.selectedModel
          ? getCompositeModelKey(selector.selectedModel)
          : defaultValue
      }
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
      disabled={effectiveDisabled || selector.modelsIsLoading}
    >
      <SelectTrigger
        aria-label="Select chat model"
        className="h-7 max-w-[18rem] border-0 bg-transparent px-2 text-xs shadow-none hover:bg-muted/50 focus:ring-0"
      >
        <div className="flex min-w-0 items-center gap-1.5">
          {triggerProvider ? (
            <ProviderIcon
              className="size-4 rounded-none bg-transparent p-0"
              providerId={getProviderIconId(triggerProvider)}
            />
          ) : null}
          <span className="truncate" title={triggerLabel}>
            {triggerLabel}
          </span>
        </div>
      </SelectTrigger>
      <SelectContent align="start" className="w-[22rem]">
        <SelectItem value={defaultValue}>
          <div className="flex min-w-0 items-center gap-2">
            {selector.defaultProvider ? (
              <ProviderIcon
                className="size-4 rounded-none bg-transparent p-0"
                providerId={getProviderIconId(selector.defaultProvider)}
              />
            ) : null}
            <span className="truncate">
              {`Organization default · ${selector.defaultLabel}`}
            </span>
          </div>
        </SelectItem>
        {(selector.models ?? []).map((model) => (
          <SelectItem
            key={getCompositeModelKey(model)}
            value={getCompositeModelKey(model)}
          >
            <div className="flex min-w-0 items-center gap-2">
              <ProviderIcon
                className="size-4 rounded-none bg-transparent p-0"
                providerId={getProviderIconId(model.model_provider)}
              />
              <span className="truncate">
                {`${model.model_name} · ${model.source_name ?? (model.source_id ? "Custom" : "Platform")}`}
              </span>
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function formatProviderLabel(value: string): string {
  return value.replaceAll("_", " ")
}

function getProviderIconId(provider: string): string {
  switch (provider) {
    case "anthropic":
      return "anthropic"
    case "azure_ai":
    case "azure_openai":
      return "microsoft"
    case "bedrock":
      return "amazon-bedrock"
    case "gemini":
    case "vertex_ai":
      return "google"
    case "openai":
      return "openai"
    default:
      return "custom-model-provider"
  }
}

function PromptModelIndicator({ modelInfo }: { modelInfo: ModelInfo }) {
  return (
    <Badge
      variant="outline"
      className="h-7 max-w-[18rem] gap-1.5 px-2.5 text-xs font-normal"
    >
      <ProviderIcon
        className="size-4 rounded-none bg-transparent p-0"
        providerId={getProviderIconId(modelInfo.provider)}
      />
      <span
        className="truncate font-medium text-foreground"
        title={modelInfo.name}
      >
        {modelInfo.name}
      </span>
      <span className="shrink-0 text-muted-foreground">
        {formatProviderLabel(modelInfo.provider)}
      </span>
    </Badge>
  )
}

function SelectedToolsOverflow({
  disabled = false,
  onClearAll,
  onRemove,
  tools,
}: {
  disabled?: boolean
  onClearAll: () => void
  onRemove: (toolName: string) => void
  tools: Array<{
    value: string
    label: string
    icon: JSX.Element
  }>
}) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (disabled) {
      setOpen(false)
    }
  }, [disabled])

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        if (disabled) {
          return
        }
        setOpen(nextOpen)
      }}
    >
      <PopoverTrigger asChild>
        <Badge
          variant="secondary"
          className="h-7 cursor-pointer gap-1.5 px-2.5 text-xs"
        >
          <span>{`+${Math.max(0, tools.length - MAX_VISIBLE_SELECTED_TOOLS)} more`}</span>
        </Badge>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-2">
        <div className="flex items-center justify-between px-1 pb-2">
          <p className="text-xs font-medium text-foreground">Selected tools</p>
          <Button
            disabled={disabled}
            onClick={() => {
              onClearAll()
              setOpen(false)
            }}
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
          >
            Clear all
          </Button>
        </div>
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {tools.map((tool) => (
            <div
              key={tool.value}
              className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="inline-flex shrink-0 items-center justify-center text-foreground">
                  {tool.icon}
                </span>
                <span className="truncate text-xs">{tool.label}</span>
              </div>
              <button
                type="button"
                className="inline-flex items-center text-muted-foreground hover:text-foreground"
                aria-label={`Remove ${tool.label}`}
                onClick={() => onRemove(tool.value)}
                disabled={disabled}
              >
                <XIcon className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function MessagePart({
  part,
  partIdx,
  id,
  role,
  status,
  isLastMessage,
  onSubmitApprovals,
}: {
  part: UIMessagePart<UIDataTypes, UITools>
  partIdx: number
  id: string
  role: UIMessage["role"]
  status?: ChatStatus
  isLastMessage: boolean
  onSubmitApprovals?: (decisions: ApprovalDecision[]) => Promise<void>
}) {
  if (part.type === "data-approval-request") {
    const payload = (part as { data?: unknown }).data
    const approvals: ApprovalCard[] = Array.isArray(payload)
      ? (payload.filter(Boolean) as ApprovalCard[])
      : []
    return (
      <ApprovalRequestPart
        key={`${id}-${partIdx}`}
        approvals={approvals}
        onSubmit={onSubmitApprovals}
      />
    )
  }

  if (part.type === "text") {
    return (
      <Message key={`${id}-${partIdx}`} from={role}>
        <MessageContent variant="flat">
          <Response>{part.text}</Response>
        </MessageContent>
      </Message>
    )
  }

  if (part.type === "reasoning") {
    return (
      <Reasoning
        key={`${id}-${partIdx}`}
        className="w-full"
        defaultOpen={status === "streaming" && isLastMessage}
        isStreaming={status === "streaming" && isLastMessage}
      >
        <ReasoningTrigger />
        <ReasoningContent>{part.text}</ReasoningContent>
      </Reasoning>
    )
  }

  if (isToolUIPart(part)) {
    const toolName = getToolName(part).replaceAll("__", ".")
    // Derive an error state for streaming when servers send
    // a tool output that encodes validation feedback in `output`
    // rather than `errorText`.
    const outputAsAny = part.output as unknown
    const partErrorText =
      typeof part === "object" && part !== null && "errorText" in part
        ? (part as { errorText?: string }).errorText
        : undefined
    const outputErrorText =
      outputAsAny &&
      typeof outputAsAny === "object" &&
      "errorText" in outputAsAny
        ? (outputAsAny as { errorText?: string }).errorText
        : undefined
    const derivedErrorText = partErrorText ?? outputErrorText
    const derivedState = derivedErrorText
      ? ("output-error" as const)
      : part.state
    return (
      <Tool key={`${id}-${partIdx}`}>
        <ToolHeader
          title={toolName}
          type={part.type}
          state={derivedState}
          icon={getIcon(toolName, {
            className: "size-5 p-[3px]",
          })}
        />
        <ToolContent>
          <ToolInput input={part.input} />
          <ToolOutput output={part.output} errorText={derivedErrorText} />
        </ToolContent>
      </Tool>
    )
  }

  return null
}

type DecisionState = {
  action: ApprovalDecision["action"] | undefined
  reason?: string
  overrideArgs?: string
}

function ApprovalRequestPart({
  approvals,
  onSubmit,
}: {
  approvals: ApprovalCard[]
  onSubmit?: (decisions: ApprovalDecision[]) => Promise<void>
}) {
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({})
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setDecisions({})
  }, [approvals.map((a) => a.tool_call_id).join(":")])

  const readyToSubmit =
    approvals.length > 0 &&
    approvals.every((approval) => decisions[approval.tool_call_id]?.action)

  const setDecision = useCallback(
    (toolCallId: string, update: Partial<DecisionState>) => {
      setDecisions((prev) => ({
        ...prev,
        [toolCallId]: {
          ...prev[toolCallId],
          ...update,
        },
      }))
    },
    []
  )

  const handleSubmit = useCallback(async () => {
    if (!onSubmit || !readyToSubmit) {
      toast({
        title: "Pending decisions",
        description: "Choose an action for each tool before continuing.",
      })
      return
    }

    const payload: ApprovalDecision[] = []
    for (const approval of approvals) {
      const decision = decisions[approval.tool_call_id]
      if (!decision?.action) {
        toast({
          title: "Missing decision",
          description: `Select an action for ${approval.tool_name}.`,
        })
        return
      }
      if (decision.action === "approve") {
        payload.push({ tool_call_id: approval.tool_call_id, action: "approve" })
      } else if (decision.action === "override") {
        try {
          const parsed = decision.overrideArgs
            ? JSON.parse(decision.overrideArgs)
            : {}
          payload.push({
            tool_call_id: approval.tool_call_id,
            action: "override",
            override_args: parsed,
          })
        } catch {
          toast({
            variant: "destructive",
            title: "Invalid JSON",
            description: `Fix override args for ${approval.tool_name} and try again.`,
          })
          return
        }
      } else if (decision.action === "deny") {
        payload.push({
          tool_call_id: approval.tool_call_id,
          action: "deny",
          reason: decision.reason ?? "",
        })
      }
    }

    try {
      setSubmitting(true)
      await onSubmit(payload)
      setDecisions({})
    } catch (error) {
      console.error(error)
    } finally {
      setSubmitting(false)
    }
  }, [approvals, decisions, onSubmit, readyToSubmit])

  const disabled = !onSubmit

  if (!approvals.length) {
    return null
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {approvals.map((approval, index) => {
          const actionId = approval.tool_name.replaceAll("__", ".")
          const decision = decisions[approval.tool_call_id]
          const initialOverrideArgs = formatArgs(approval.args)
          const isLastApproval = index === approvals.length - 1
          return (
            <div
              key={approval.tool_call_id}
              className="space-y-3 rounded-md border border-border/60 bg-background p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2.5">
                    {getIcon(actionId, {
                      className: "size-5 p-[3px]",
                    })}
                    <p className="font-medium text-sm">{actionId}</p>
                    {getStatusBadge("approval-requested")}
                  </div>
                </div>
                <JsonViewWithControls
                  src={approval.args}
                  className="max-h-64"
                  showControls={false}
                  defaultExpanded
                />
                <div className="flex w-full flex-wrap justify-start gap-1 sm:w-auto [&>button]:h-6 [&>button]:rounded-lg">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || submitting}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "approve",
                        reason: undefined,
                        overrideArgs: undefined,
                      })
                    }
                    className={cn(
                      decision?.action === "approve" &&
                        "border-success bg-background text-success hover:bg-success/10 hover:text-success"
                    )}
                  >
                    <CheckIcon className="mr-1 size-3" />
                    Approve
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || submitting}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "override",
                        reason: undefined,
                        overrideArgs:
                          decision?.overrideArgs ?? initialOverrideArgs,
                      })
                    }
                    className={cn(
                      decision?.action === "override" &&
                        "border-success bg-background text-success hover:bg-success/10 hover:text-success"
                    )}
                  >
                    <PencilIcon className="mr-1 size-3" />
                    Edit + approve
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || submitting}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "deny",
                        overrideArgs: undefined,
                      })
                    }
                    className={cn(
                      decision?.action === "deny" &&
                        "border-destructive bg-background text-destructive hover:bg-destructive/10 hover:text-destructive"
                    )}
                  >
                    <XIcon className="mr-1 size-3" />
                    Deny
                  </Button>
                </div>
              </div>
              {decision?.action === "override" && (
                <div
                  data-testid={`approval-override-editor-${approval.tool_call_id}`}
                >
                  <CodeEditor
                    value={decision.overrideArgs ?? initialOverrideArgs}
                    language="json"
                    onChange={(value) =>
                      setDecision(approval.tool_call_id, {
                        ...decision,
                        overrideArgs: value,
                      })
                    }
                    className={cn(
                      "text-xs",
                      "[&_.cm-editor]:!border [&_.cm-editor]:!border-input [&_.cm-editor]:!bg-background [&_.cm-editor]:rounded-md",
                      "[&_.cm-scroller]:h-auto [&_.cm-scroller]:min-h-24 [&_.cm-scroller]:max-h-80 [&_.cm-scroller]:overflow-auto"
                    )}
                  />
                </div>
              )}
              {decision?.action === "deny" && (
                <Textarea
                  className="text-xs"
                  rows={3}
                  value={decision.reason ?? ""}
                  onChange={(event) =>
                    setDecision(approval.tool_call_id, {
                      ...decision,
                      reason: event.target.value,
                    })
                  }
                  placeholder="Share a short reason"
                  disabled={disabled || submitting}
                />
              )}
              {isLastApproval && (
                <div className="flex flex-wrap justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    onClick={handleSubmit}
                    disabled={disabled || submitting || !readyToSubmit}
                    className="h-6 gap-1 px-2 text-xs"
                  >
                    {submitting ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <CheckIcon className="size-3" />
                    )}
                    {submitting ? "Submitting..." : "Submit"}
                  </Button>
                </div>
              )}
            </div>
          )
        })}
      </div>
      {disabled && (
        <p className="text-xs text-muted-foreground">
          Approval submission is not available in this context.
        </p>
      )}
    </div>
  )
}

function formatArgs(args: unknown): string {
  if (args == null) return "{}"
  if (typeof args === "string") {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  }
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}
