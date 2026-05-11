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
  MousePointer2OffIcon,
  MousePointerClickIcon,
  PencilIcon,
  RefreshCcwIcon,
  XIcon,
} from "lucide-react"
import { motion } from "motion/react"
import {
  type ChangeEvent,
  type FocusEvent,
  type KeyboardEvent,
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type {
  AgentPresetReadMinimal,
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
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector"
import {
  PromptInput,
  PromptInputBody,
  PromptInputButton,
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
import { useBuilderRegistryActions } from "@/lib/hooks"
import { cn } from "@/lib/utils"

const MAX_TOOL_MENTION_RESULTS = 40
const AGENT_TOOL_NAMES = new Set(["Agent", "Task"])
const AGENT_TOOL_TARGET_KEYS = ["subagent_type", "agent_type", "type", "name"]
const AGENT_TOOL_NESTED_INPUT_KEYS = ["args", "input", "tool_input"]

type ToolMentionToken = {
  start: number
  end: number
  query: string
}

type ToolMentionState = ToolMentionToken & {
  activeIndex: number
}

const TOOL_ICON_PROPS = { className: "size-5 p-[3px]" } as const

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

type ChatPresetSelector = {
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
  /**
   * Optional preset selector rendered in the prompt footer.
   */
  presetSelector?: ChatPresetSelector
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
  autoFocusInput = false,
  onBeforeSend,
  pendingMessage,
  onPendingMessageSent,
  inputDisabled = false,
  inputDisabledPlaceholder,
  presetSelector,
}: ChatSessionPaneProps) {
  const queryClient = useQueryClient()
  const promptInputContainerRef = useRef<HTMLDivElement>(null)
  const promptTextareaFocusedRef = useRef(false)
  const shouldRestoreInputFocusRef = useRef(false)
  const promptPointerDownInsideRef = useRef(false)
  const promptPointerDownResetTimerRef = useRef<number>()
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

  const isInputDisabled = isReadonly || inputDisabled || !canSubmit
  const isInputDisabledRef = useRef(isInputDisabled)
  isInputDisabledRef.current = isInputDisabled
  const wasInputDisabledRef = useRef(isInputDisabled)

  useEffect(() => {
    const wasInputDisabled = wasInputDisabledRef.current
    wasInputDisabledRef.current = isInputDisabled

    if (!wasInputDisabled && isInputDisabled) {
      shouldRestoreInputFocusRef.current = promptTextareaFocusedRef.current
      return
    }

    if (!wasInputDisabled || isInputDisabled) {
      return
    }

    if (!shouldRestoreInputFocusRef.current) {
      return
    }
    shouldRestoreInputFocusRef.current = false

    const textarea =
      promptInputContainerRef.current?.querySelector<HTMLTextAreaElement>(
        'textarea[name="message"]'
      )
    textarea?.focus()
  }, [isInputDisabled])

  useEffect(() => {
    if (!isInputDisabled) {
      return
    }

    function cancelPendingFocusRestore(event: Event) {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      if (promptInputContainerRef.current?.contains(target)) {
        return
      }
      shouldRestoreInputFocusRef.current = false
      promptTextareaFocusedRef.current = false
    }

    document.addEventListener("pointerdown", cancelPendingFocusRestore, true)
    document.addEventListener("focusin", cancelPendingFocusRestore, true)

    return () => {
      document.removeEventListener(
        "pointerdown",
        cancelPendingFocusRestore,
        true
      )
      document.removeEventListener("focusin", cancelPendingFocusRestore, true)
    }
  }, [isInputDisabled])

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
    const nextTools = chat?.tools ?? []

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
  }, [chat?.id, chat?.tools])

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

  const handleInputFocus = useCallback(() => {
    promptTextareaFocusedRef.current = true
  }, [])

  const handlePromptPointerDownCapture = useCallback(() => {
    promptPointerDownInsideRef.current = true
    if (promptPointerDownResetTimerRef.current !== undefined) {
      window.clearTimeout(promptPointerDownResetTimerRef.current)
    }
    promptPointerDownResetTimerRef.current = window.setTimeout(() => {
      promptPointerDownInsideRef.current = false
      promptPointerDownResetTimerRef.current = undefined
    }, 0)
  }, [])

  const handleInputBlur = useCallback(
    (event: FocusEvent<HTMLTextAreaElement>) => {
      setToolMention(undefined)

      const nextFocusedElement = event.relatedTarget
      if (
        nextFocusedElement instanceof Node &&
        promptInputContainerRef.current?.contains(nextFocusedElement)
      ) {
        return
      }

      if (nextFocusedElement === null && promptPointerDownInsideRef.current) {
        return
      }

      if (!isInputDisabledRef.current) {
        promptTextareaFocusedRef.current = false
        shouldRestoreInputFocusRef.current = false
      }
    },
    []
  )

  useEffect(() => {
    return () => {
      if (promptPointerDownResetTimerRef.current !== undefined) {
        window.clearTimeout(promptPointerDownResetTimerRef.current)
      }
    }
  }, [])

  const selectedToolBadges = useMemo(
    () =>
      selectedTools.map((toolName) => {
        const suggestion = toolSuggestionMap.get(toolName)
        return {
          value: toolName,
          label: suggestion?.label ?? toolName,
          icon: getIcon(toolName, {
            className: "size-5 shrink-0",
          }),
        }
      }),
    [selectedTools, toolSuggestionMap]
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
              const sourceUrlParts = parts?.filter(
                (part) => part.type === "source-url"
              )
              return (
                <div key={id} className="group relative">
                  {role === "assistant" &&
                    sourceUrlParts &&
                    sourceUrlParts.length > 0 && (
                      <Sources>
                        <SourcesTrigger count={sourceUrlParts.length} />
                        {sourceUrlParts.map((part, partIdx) => (
                          <SourcesContent key={`${id}-${part.type}-${partIdx}`}>
                            <Source
                              href={"url" in part ? part.url : "#"}
                              title={"url" in part ? part.url : "Source"}
                            />
                          </SourcesContent>
                        ))}
                      </Sources>
                    )}

                  {parts?.map((part, partIdx) => (
                    <MemoizedMessagePart
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
      <div
        className="relative px-3 pb-3"
        onPointerDownCapture={handlePromptPointerDownCapture}
        ref={promptInputContainerRef}
      >
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
              {selectedToolBadges.map((tool) => (
                <Badge
                  key={tool.value}
                  variant="secondary"
                  className="h-7 gap-1.5 px-2.5 text-xs"
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
            </PromptInputHeader>
          )}
          <PromptInputBody>
            <PromptInputTextarea
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onFocus={handleInputFocus}
              onBlur={handleInputBlur}
              placeholder={
                isReadonly
                  ? "This is a legacy session (read-only)"
                  : inputDisabled && inputDisabledPlaceholder
                    ? inputDisabledPlaceholder
                    : placeholder
              }
              value={input}
              autoFocus={autoFocusInput && !isReadonly && !inputDisabled}
              disabled={isInputDisabled}
            />
          </PromptInputBody>
          <PromptInputFooter>
            <PromptInputTools>
              {presetSelector && !isReadonly && (
                <PromptPresetSelector
                  selector={presetSelector}
                  disabled={inputDisabled || !canSubmit}
                />
              )}
              {!isReadonly ? (
                <PromptModelIndicator modelInfo={modelInfo} />
              ) : null}
            </PromptInputTools>
            <PromptInputSubmit
              disabled={isInputDisabled || !input.trim()}
              status={status}
              className="text-muted-foreground/80"
            />
          </PromptInputFooter>
        </PromptInput>
      </div>
    </div>
  )
}

function PromptPresetSelector({
  selector,
  disabled = false,
}: {
  selector: ChatPresetSelector
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)

  const effectiveDisabled = Boolean(disabled || selector.disabled)

  useEffect(() => {
    if (effectiveDisabled) {
      setOpen(false)
    }
  }, [effectiveDisabled])

  const errorMessage = useMemo(() => {
    if (typeof selector.presetsError === "string") {
      return selector.presetsError
    }
    if (
      selector.presetsError &&
      typeof selector.presetsError === "object" &&
      "body" in selector.presetsError &&
      typeof (selector.presetsError as { body?: { detail?: unknown } }).body
        ?.detail === "string"
    ) {
      return (selector.presetsError as { body?: { detail?: string } }).body
        ?.detail
    }
    if (
      selector.presetsError &&
      typeof selector.presetsError === "object" &&
      "message" in selector.presetsError &&
      typeof (selector.presetsError as { message?: unknown }).message ===
        "string"
    ) {
      return (selector.presetsError as { message: string }).message
    }
    return "Failed to load presets"
  }, [selector.presetsError])

  const noPresetValue = "__workspace_default_preset__"

  const handleSelect = (value: string) => {
    setOpen(false)
    void selector.onSelect(value === noPresetValue ? null : value)
  }
  const PresetIcon =
    selector.selectedPresetId === null
      ? MousePointer2OffIcon
      : MousePointerClickIcon

  return (
    <ModelSelector
      open={open}
      onOpenChange={(nextOpen) => {
        if (effectiveDisabled) {
          return
        }
        setOpen(nextOpen)
      }}
    >
      <ModelSelectorTrigger asChild>
        <PromptInputButton
          size="sm"
          variant="ghost"
          disabled={effectiveDisabled}
          className="h-7 max-w-[16rem] justify-start gap-1.5 px-2 text-xs"
          aria-label="Select preset agent"
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <PresetIcon className="size-3 text-muted-foreground" />
            <span className="truncate" title={selector.label}>
              {selector.label}
            </span>
          </span>
          {selector.showSpinner ? (
            <span className="ml-auto inline-flex items-center">
              <Loader2 className="size-3 animate-spin text-muted-foreground" />
            </span>
          ) : null}
        </PromptInputButton>
      </ModelSelectorTrigger>
      <ModelSelectorContent title="Select preset agent" className="sm:max-w-lg">
        {selector.presetsIsLoading ? (
          <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            Loading presets...
          </div>
        ) : selector.presetsError ? (
          <div className="p-3 text-xs text-red-600">{errorMessage}</div>
        ) : (
          <>
            <ModelSelectorInput
              placeholder="Search presets..."
              className="text-xs"
            />
            <ModelSelectorList className="max-h-64 overflow-y-auto">
              <ModelSelectorEmpty className="py-4 text-xs text-muted-foreground">
                No presets found.
              </ModelSelectorEmpty>
              <ModelSelectorGroup>
                <ModelSelectorItem
                  value="no preset"
                  onSelect={() => handleSelect(noPresetValue)}
                  className="flex items-start justify-between gap-2 py-2 text-xs"
                >
                  <div className="flex flex-col">
                    <span className="font-medium">No preset</span>
                    <span className="text-muted-foreground">
                      {selector.noPresetDescription ??
                        "Use workspace default agent instructions."}
                    </span>
                  </div>
                  {selector.selectedPresetId === null ? (
                    <CheckIcon className="mt-0.5 size-3.5" />
                  ) : null}
                </ModelSelectorItem>
                {(selector.presets ?? []).map((preset) => (
                  <ModelSelectorItem
                    key={preset.id}
                    value={`${preset.name} ${preset.description ?? ""}`}
                    onSelect={() => handleSelect(preset.id)}
                    className="flex items-start justify-between gap-2 py-2 text-xs"
                  >
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate font-medium">
                        {preset.name}
                      </span>
                      {preset.description ? (
                        <span className="text-muted-foreground">
                          {preset.description}
                        </span>
                      ) : null}
                    </div>
                    {selector.selectedPresetId === preset.id ? (
                      <CheckIcon className="mt-0.5 size-3.5" />
                    ) : null}
                  </ModelSelectorItem>
                ))}
              </ModelSelectorGroup>
            </ModelSelectorList>
          </>
        )}
      </ModelSelectorContent>
    </ModelSelector>
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
      return "custom"
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
        providerId={modelInfo.iconId ?? getProviderIconId(modelInfo.provider)}
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
    const toolTitle = getToolTitle(toolName, part.input)
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
          title={toolTitle}
          type={part.type}
          state={derivedState}
          icon={getIcon(toolName, TOOL_ICON_PROPS)}
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

function getToolTitle(toolName: string, input: unknown): string {
  if (!AGENT_TOOL_NAMES.has(toolName)) {
    return toolName
  }

  const agentTarget = getAgentToolTarget(input)
  return agentTarget ? `${toolName}: ${agentTarget}` : toolName
}

function getAgentToolTarget(input: unknown, depth = 0): string | null {
  if (depth > 2) {
    return null
  }

  const inputRecord = asInputRecord(input)
  if (!inputRecord) {
    return null
  }

  for (const key of AGENT_TOOL_TARGET_KEYS) {
    const value = inputRecord[key]
    if (typeof value === "string" && value.trim()) {
      return value.trim()
    }
  }

  for (const key of AGENT_TOOL_NESTED_INPUT_KEYS) {
    const nestedTarget = getAgentToolTarget(inputRecord[key], depth + 1)
    if (nestedTarget) {
      return nestedTarget
    }
  }

  return null
}

function asInputRecord(input: unknown): Record<string, unknown> | null {
  if (typeof input === "string") {
    const trimmed = input.trim()
    if (!trimmed) {
      return null
    }
    try {
      const parsed: unknown = JSON.parse(trimmed)
      return asInputRecord(parsed)
    } catch {
      return null
    }
  }

  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return null
  }

  return input as Record<string, unknown>
}

const MemoizedMessagePart = memo(MessagePart, (prev, next) => {
  if (
    prev.partIdx !== next.partIdx ||
    prev.id !== next.id ||
    prev.role !== next.role ||
    prev.status !== next.status ||
    prev.isLastMessage !== next.isLastMessage ||
    prev.onSubmitApprovals !== next.onSubmitApprovals
  ) {
    return false
  }

  const prevPart = prev.part
  const nextPart = next.part

  if (prevPart.type !== nextPart.type) {
    return false
  }

  if (prevPart.type === "text" || prevPart.type === "reasoning") {
    return prevPart.text === (nextPart as typeof prevPart).text
  }

  if (isToolUIPart(prevPart) && isToolUIPart(nextPart)) {
    return (
      prevPart.toolCallId === nextPart.toolCallId &&
      prevPart.state === nextPart.state &&
      prevPart.input === nextPart.input &&
      prevPart.output === nextPart.output &&
      ("errorText" in prevPart
        ? (prevPart as { errorText?: string }).errorText
        : undefined) ===
        ("errorText" in nextPart
          ? (nextPart as { errorText?: string }).errorText
          : undefined)
    )
  }

  return prevPart === nextPart
})
MemoizedMessagePart.displayName = "MessagePart"

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

  const approvalsKey = useMemo(
    () => approvals.map((a) => a.tool_call_id).join(":"),
    [approvals]
  )

  useEffect(() => {
    setDecisions({})
  }, [approvalsKey])

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
                    {getIcon(actionId, TOOL_ICON_PROPS)}
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
