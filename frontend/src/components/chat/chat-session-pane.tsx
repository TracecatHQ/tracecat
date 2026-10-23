"use client"

import {
  type ChatOnDataCallback,
  type ChatStatus,
  getToolName,
  isToolUIPart,
  type UIMessage,
} from "ai"
import { Loader2, MousePointerClickIcon, XIcon } from "lucide-react"
import { motion } from "motion/react"
import {
  type ChangeEvent,
  type FocusEvent,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useContext,
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
import { Response } from "@/components/ai-elements/response"
import {
  Source,
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { AgentTurn, groupThread } from "@/components/chat/agent-turn"
import {
  AGENT_EXECUTE_REQUIRED_MESSAGE,
  ApprovalComposerContext,
  ApprovalRequestPart,
  ApprovalsAlreadyDecidedError,
} from "@/components/chat/approval-request-part"
import { ChatEmptyHero } from "@/components/chat/chat-empty-hero"
import { ChatToolsPicker } from "@/components/chat/chat-tools-picker"
import { RetryAction, TurnErrorLine } from "@/components/chat/chat-turn-error"
import {
  getRunningLineLabel,
  isAwaitingOutput,
  MessageParts,
  RunningLine,
} from "@/components/chat/message-part"
import { getIcon, ProviderIcon } from "@/components/icons"
import { MentionHint } from "@/components/mentions/mention-hint"
import { MentionPopover } from "@/components/mentions/mention-popover"
import { Badge } from "@/components/ui/badge"
import { Trace } from "@/components/ui/trace"
import { toast } from "@/components/ui/use-toast"
import { extensions } from "@/extensions"
import {
  makeContinueMessage,
  parseChatError,
  useAdoptServerTranscript,
  useCancelChatTurn,
  useGetChatVercel,
  usePendingMessages,
  useUpdateChat,
  useVercelChat,
} from "@/hooks/use-chat"
import {
  ChatVerbosityContext,
  getChatVerbosityPolicy,
  useChatVerbosityPreference,
} from "@/hooks/use-chat-verbosity"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { useMentions } from "@/hooks/use-mentions"
import { useOverflowBadges } from "@/hooks/use-overflow-badges"
import { SubagentStreamContext } from "@/hooks/use-subagent-stream"
import { isAgentToolSelectable } from "@/lib/agent-tools"
import {
  CANCELLED_DATA_PART_TYPE,
  collectCancelledTurns,
  ENTITY_TO_INVALIDATION,
  getPendingApprovalIds,
  getSessionLastError,
  isApprovalCardArray,
  type ModelInfo,
  markLiveDenials,
  toUIMessage,
  transformMessages,
  withoutStreamedError,
} from "@/lib/chat"
import type { RunningComposer } from "@/lib/extensions"
import { useBuilderRegistryActions, useListMcpIntegrations } from "@/lib/hooks"
import { findAgentMention, type MentionRange } from "@/lib/mentions"
import { useQueryClient } from "@/lib/query"
import { cn } from "@/lib/utils"
import { PageContext } from "@/providers/chat-page-context"
import type { ChatSurface } from "@/types/chat-surface"
import { ARTIFACT_DATA_PART_TYPE } from "@/types/workspace-chat-artifacts"

/** How often an idle pane checks whether a waiting approval was decided. */
const APPROVAL_REFRESH_MS = 5_000

/** Whether a message draws anything in the thread. */
function messageHasVisibleParts(message: Pick<UIMessage, "parts">): boolean {
  return message.parts.some((part) => part.type !== ARTIFACT_DATA_PART_TYPE)
}

/**
 * Whether a message is a request the server can run as a turn: a user prompt
 * with text, or an approval submission. Reloaded history can also hold
 * user-role tool results, which are neither.
 */
function isTurnRequest({ role, parts }: UIMessage): boolean {
  return (
    role === "user" &&
    parts.some(
      (part) =>
        (part.type === "text" && part.text.length > 0) ||
        part.type === "data-continue"
    )
  )
}

/**
 * Raised to abandon a submit that sent no message.
 *
 * `submitPrompt` runs its cleanup after any submit that resolves, clearing
 * attachments and blanking the textarea, so a turn that produced nothing has to
 * reject to leave the composer alone. It swallows the rejection. Anything worth
 * telling the user is reported before this is thrown.
 */
class SubmitAbandonedError extends Error {
  constructor(reason: string) {
    super(reason)
    this.name = "SubmitAbandonedError"
  }
}

/** The installed running-composer slot; Tracecat leaves the composer disabled. */
const useRunningComposer =
  extensions.useRunningComposer ??
  ((): RunningComposer | undefined => undefined)

function matchingUserTextPartKeys(
  messages: UIMessage[],
  text: string
): Set<string> {
  const keys = new Set<string>()
  for (const message of messages) {
    if (message.role !== "user") {
      continue
    }
    for (const [partIndex, part] of message.parts.entries()) {
      if (part.type === "text" && part.text === text) {
        keys.add(`${message.id}:${partIndex}`)
      }
    }
  }
  return keys
}

function hasNewMatchingUserTextPart(
  messages: UIMessage[],
  text: string,
  knownKeys: Set<string>
): boolean {
  for (const key of matchingUserTextPartKeys(messages, text)) {
    if (!knownKeys.has(key)) {
      return true
    }
  }
  return false
}

function areToolListsEqual(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((tool, index) => tool === right[index])
  )
}

/**
 * Session preset wiring for the composer. The list itself comes from the
 * mention popover, so this only carries the active selection and the write.
 */
type ChatPresetSelector = {
  label: string
  selectedPresetId: string | null
  /**
   * Awaited before a message is sent so the preset lands on the same turn.
   * Resolves false when the write failed, which aborts the send.
   */
  onSelect: (presetId: string | null) => Promise<boolean>
  disabled?: boolean
  showSpinner?: boolean
}

export interface ChatSessionPaneProps {
  chat?: AgentSessionReadVercel | ChatReadVercel
  workspaceId: string
  entityType?: AgentSessionEntity
  entityId?: string
  className?: string
  placeholder?: string
  onMessagesChange?: (messages: UIMessage[]) => void
  onData?: ChatOnDataCallback<UIMessage>
  /** Called whenever the underlying chat transport status changes. */
  onStatusChange?: (status: ChatStatus) => void
  modelInfo?: ModelInfo
  /** Shown in the message area until the conversation has content. */
  emptyState?: ReactNode
  toolsEnabled?: boolean
  /** Whether `@` offers agent presets on this surface. */
  agentMentionsSupported?: boolean
  mcpEnabled?: boolean
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
    selectedTools?: string[],
    selectedMcpIntegrations?: string[]
  ) => Promise<string | null>
  /**
   * Render a temporary user message and assistant loading dots while
   * onBeforeSend is creating a session or fork before the real stream mounts.
   */
  optimisticBeforeSend?: boolean
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
  /** Selects the chat stream projection and surrounding UI surface. */
  surface?: ChatSurface
  /** Center the thread and prompt in a readable column. Defaults to on for workspace chat. */
  centered?: boolean
  /**
   * Reconnect to the live event stream on mount. Defaults to true. Set to
   * false for terminal sessions whose history is already loaded, to avoid
   * replaying the last turn on top of the seeded messages.
   */
  resume?: boolean
}

export { AGENT_EXECUTE_REQUIRED_MESSAGE }

export function ChatSessionPane({
  chat,
  workspaceId,
  entityType,
  entityId,
  className,
  placeholder = "Ask your question...",
  onMessagesChange,
  onData,
  onStatusChange,
  modelInfo,
  emptyState,
  toolsEnabled = true,
  agentMentionsSupported = false,
  mcpEnabled = false,
  autoFocusInput = false,
  onBeforeSend,
  optimisticBeforeSend = false,
  pendingMessage,
  onPendingMessageSent,
  inputDisabled = false,
  inputDisabledPlaceholder,
  presetSelector,
  surface = "regular",
  centered,
  resume = true,
}: ChatSessionPaneProps) {
  const queryClient = useQueryClient()
  const pageContext = useContext(PageContext)
  const formsAccess = useFormsAccess()
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
  const [isInputFocused, setIsInputFocused] = useState(false)
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const getInputText = useCallback(() => input, [input])
  const mentions = useMentions({
    workspaceId,
    textareaRef: promptTextareaRef,
    getText: getInputText,
    setText: setInput,
    // Chat offers no `/` workflow commands.
    agents: agentMentionsSupported
      ? { entitlements: [], single: true }
      : undefined,
  })
  const {
    handleTextChange: handleMentionTextChange,
    handleKeyDown: handleMentionKeyDown,
    dismiss: dismissMentions,
    dropPrefix: dropMentionPrefix,
    applySplice: applyMentionSplice,
  } = mentions
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const [selectedMcpIntegrations, setSelectedMcpIntegrations] = useState<
    string[]
  >([])
  const [optimisticMessageText, setOptimisticMessageText] = useState<
    string | null
  >(null)
  const optimisticMessageKnownTextPartKeysRef = useRef<Set<string>>(new Set())
  const { updateChat, isUpdating: isUpdatingTools } = useUpdateChat(workspaceId)
  const { cancelChatTurn, isCancellingChatTurn } =
    useCancelChatTurn(workspaceId)
  const { registryActions } = useBuilderRegistryActions()
  const sessionMcpEnabled = mcpEnabled && entityType === "copilot"
  const { mcpIntegrations } = useListMcpIntegrations(workspaceId, undefined, {
    enabled: toolsEnabled && sessionMcpEnabled,
  })

  // Read-only state includes ownership, backend availability, and entitlements.
  const isReadonly = chat ? "is_readonly" in chat && chat.is_readonly : false
  // Sending, retrying and approving all run the agent. `undefined` means the
  // scopes are still loading, which must not disable anything.
  const cannotExecuteAgents = useScopeCheck("agent:execute") === false
  let readonlyDescription =
    chat && "user_id" in chat
      ? "This legacy conversation is read-only."
      : "This conversation is read-only."
  if (chat && "spawned_by_session_id" in chat && chat.spawned_by_session_id) {
    readonlyDescription =
      "This subagent conversation is read-only. Message the parent conversation instead."
  }

  const uiMessages = useMemo(
    () => (chat?.messages || []).map(toUIMessage),
    [chat?.messages]
  )
  const isWorkspaceChat = surface === "workspace-chat"
  const isCentered = centered ?? isWorkspaceChat
  const chatContentCenterClass = isCentered
    ? "mx-auto w-full max-w-[56rem]"
    : undefined
  const promptCenterClass = isCentered
    ? "relative mx-auto w-full max-w-[56rem]"
    : "relative"
  const promptInputClassName = isCentered
    ? "[&_[data-slot=input-group]]:rounded-2xl [&_[data-slot=input-group]]:border-muted-foreground/25 [&_[data-slot=input-group]]:shadow-none"
    : undefined
  // A session opened to send its first message has no stream to resume, and a
  // resume that finds none resets the send's status. Only that is frozen per
  // chat; later resume changes still re-arm the reconnect.
  const [openedToSend, setOpenedToSend] = useState(() => ({
    chatId: chat?.id,
    value: Boolean(pendingMessage),
  }))
  if (openedToSend.chatId !== chat?.id) {
    setOpenedToSend({ chatId: chat?.id, value: Boolean(pendingMessage) })
  }
  const {
    sendMessage,
    messages,
    setMessages,
    status,
    lastError,
    lastErrorDisconnected,
    clearError,
    retryTurn,
    subagentStore,
  } = useVercelChat({
    chatId: chat?.id,
    workspaceId,
    messages: uiMessages,
    modelInfo,
    onData,
    pageContext,
    resume: resume && !openedToSend.value,
  })
  const [verbosity] = useChatVerbosityPreference()
  const verbosityPolicy = getChatVerbosityPolicy(verbosity)
  const subagentStreamContext = useMemo(
    () =>
      subagentStore ? { store: subagentStore, workspaceId, status } : null,
    [subagentStore, workspaceId, status]
  )

  // Prefer the live streaming error; fall back to the persisted last_error so a
  // reopened session whose last run failed still surfaces why (the live error
  // only exists during/right after the failing run).
  //
  // Suppress both the moment a new turn starts — optimistically on submit,
  // before the backend clears last_error — because the new run (or a reload's
  // replay of a dropped one) produces its own outcome that supersedes them.
  const hasNewTurnStarted =
    optimisticMessageText !== null ||
    status === "submitted" ||
    status === "streaming"
  const persistedError = chat ? getSessionLastError(chat) : null
  const displayedError = hasNewTurnStarted
    ? null
    : (lastError ?? persistedError)

  useAdoptServerTranscript({
    status,
    serverMessages: uiMessages,
    liveMessages: messages,
    setMessages,
  })

  // Another tab or the Inbox can decide an approval this pane shows as
  // waiting. Poll the session while one waits and the pane is idle, so the
  // decision replaces the stale card through the adoption above.
  const awaitingApproval = useMemo(
    () => status === "ready" && getPendingApprovalIds(messages).size > 0,
    [status, messages]
  )
  useGetChatVercel({
    chatId: chat?.id,
    workspaceId,
    enabled: awaitingApproval,
    refetchInterval: awaitingApproval ? APPROVAL_REFRESH_MS : false,
  })

  useEffect(() => {
    onStatusChange?.(status)
  }, [status, onStatusChange])

  const hasOptimisticMessageInStream = useMemo(
    () =>
      optimisticMessageText
        ? hasNewMatchingUserTextPart(
            messages,
            optimisticMessageText,
            optimisticMessageKnownTextPartKeysRef.current
          )
        : false,
    [messages, optimisticMessageText]
  )

  useEffect(() => {
    if (hasOptimisticMessageInStream) {
      optimisticMessageKnownTextPartKeysRef.current = new Set()
      setOptimisticMessageText(null)
    }
  }, [hasOptimisticMessageInStream])

  // Track pending message sends to avoid duplicate sends
  const pendingMessageSentRef = useRef<string | null>(null)

  // Send pending message on mount (used after forking)
  useEffect(() => {
    if (!pendingMessage || isReadonly || cannotExecuteAgents) return
    if (!chat || !modelInfo) return

    const messageKey = `${chat.id}:${pendingMessage}`
    if (pendingMessageSentRef.current === messageKey) return

    pendingMessageSentRef.current = messageKey
    clearError()
    sendMessage({ text: pendingMessage })
    onPendingMessageSent?.()
  }, [
    pendingMessage,
    isReadonly,
    cannotExecuteAgents,
    chat,
    modelInfo,
    clearError,
    sendMessage,
    onPendingMessageSent,
  ])

  // Allow input when ready OR in error state (so user can retry after transient failures)
  const canSubmit = status === "ready" || status === "error"
  const isGeneratingTurn = status === "submitted" || status === "streaming"
  const capabilities =
    chat && "capabilities" in chat ? (chat.capabilities ?? {}) : {}
  const { pending, refreshPending, withdraw } = usePendingMessages({
    workspaceId,
    chatId: chat?.id,
    enabled: isGeneratingTurn && !!capabilities.listing_pending,
  })
  const restoreInput = useCallback(
    (text: string) => {
      const draft = promptTextareaRef.current?.value ?? ""
      const prefix = draft ? `${text}\n` : text
      // Shift mention ranges past the restored text before it lands.
      applyMentionSplice({ start: 0, deleted: 0, inserted: prefix.length })
      setInput(prefix + draft)
    },
    [applyMentionSplice]
  )
  const runningComposer = useRunningComposer({
    chatId: chat?.id,
    workspaceId,
    capabilities,
    running: isGeneratingTurn,
    messages,
    pending,
    refreshPending,
    withdraw,
    restoreInput,
  })
  // Takes the composer's input while a turn runs, when an extension offers it.
  const steerComposer =
    isGeneratingTurn && !isReadonly && !cannotExecuteAgents
      ? runningComposer
      : undefined
  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (handleMentionKeyDown(event)) return
    if (event.key === "Escape" && steerComposer?.onEscape) {
      event.preventDefault()
      steerComposer.onEscape()
    }
  }

  const isWaitingForResponse = useMemo(
    () => isAwaitingOutput(status, messages),
    [status, messages]
  )
  const isOptimisticBeforeSendPending = optimisticMessageText !== null
  const isInputDisabled =
    isReadonly ||
    cannotExecuteAgents ||
    inputDisabled ||
    isOptimisticBeforeSendPending ||
    (!canSubmit && !steerComposer)

  let composerPlaceholder = steerComposer?.placeholder ?? placeholder
  if (isReadonly) {
    composerPlaceholder = readonlyDescription
  } else if (cannotExecuteAgents) {
    composerPlaceholder = AGENT_EXECUTE_REQUIRED_MESSAGE
  } else if (
    (inputDisabled || isOptimisticBeforeSendPending) &&
    inputDisabledPlaceholder
  ) {
    composerPlaceholder = inputDisabledPlaceholder
  }

  const [cancelRequested, setCancelRequested] = useState(false)

  useEffect(() => {
    if (!isGeneratingTurn) setCancelRequested(false)
  }, [isGeneratingTurn])

  const handleStop = useCallback(async () => {
    if (!chat?.id || cancelRequested || isReadonly || cannotExecuteAgents)
      return
    setCancelRequested(true)
    try {
      await cancelChatTurn({ chatId: chat.id })
    } catch (error) {
      setCancelRequested(false)
      toast({
        title: "Failed to stop run",
        description: parseChatError(error),
      })
    }
  }, [
    cancelChatTurn,
    cancelRequested,
    chat?.id,
    isReadonly,
    cannotExecuteAgents,
  ])
  const isInputDisabledRef = useRef(isInputDisabled)
  isInputDisabledRef.current = isInputDisabled
  /** Guards `handleSubmit` against re-entry while it awaits a preset write. */
  const submitInFlightRef = useRef(false)
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

  /**
   * Approval IDs the session still waits on, read fresh from the server, or
   * null when it cannot be read. A waiting approval's card is always in the
   * session's history, so a missing card means it was decided.
   */
  const fetchPendingApprovalIds = useCallback(async () => {
    if (!chat?.id) return null
    const queryKey = ["chat", chat.id, workspaceId, "vercel"]
    try {
      await queryClient.refetchQueries(
        { queryKey, exact: true },
        { throwOnError: true }
      )
    } catch {
      return null
    }
    const fresh = queryClient.getQueryData<
      AgentSessionReadVercel | ChatReadVercel
    >(queryKey)
    return fresh
      ? getPendingApprovalIds((fresh.messages ?? []).map(toUIMessage))
      : null
  }, [chat?.id, queryClient, workspaceId])

  const [sentDecisions, setSentDecisions] = useState<ApprovalDecision[]>([])
  const handleSubmitApprovals = useCallback(
    async (decisionPayload: ApprovalDecision[]): Promise<string[]> => {
      if (isReadonly || !decisionPayload.length) return []
      // Another tab or the Inbox may have decided these since this pane last
      // refreshed, so submit only the decisions the session still waits on.
      const pendingIds = await fetchPendingApprovalIds()
      const decisions = pendingIds
        ? decisionPayload.filter(({ tool_call_id }) =>
            pendingIds.has(tool_call_id)
          )
        : decisionPayload
      if (decisions.length === 0) {
        toast({
          title: "Already decided",
          description:
            "This approval was decided elsewhere. The chat now shows the outcome.",
        })
        throw new ApprovalsAlreadyDecidedError()
      }
      try {
        clearError()
        await sendMessage(makeContinueMessage(decisions))
        setSentDecisions((prev) => [...prev, ...decisions])
        return decisions.map(({ tool_call_id }) => tool_call_id)
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
    [clearError, fetchPendingApprovalIds, isReadonly, sendMessage]
  )

  const [sentApprovalIds, setSentApprovalIds] = useState<ReadonlySet<string>>(
    () => new Set()
  )
  const [deniedApprovalIds, setDeniedApprovalIds] = useState<
    ReadonlySet<string>
  >(() => new Set())
  const handleSubmitComposerApprovals = useCallback(
    async (decisionPayload: ApprovalDecision[]): Promise<string[]> => {
      const sentIds = await handleSubmitApprovals(decisionPayload)
      const sent = new Set(sentIds)
      const deniedIds = decisionPayload
        .filter(
          ({ action, tool_call_id }) =>
            action === "deny" && sent.has(tool_call_id)
        )
        .map(({ tool_call_id }) => tool_call_id)
      setSentApprovalIds((prev) => new Set([...prev, ...sentIds]))
      setDeniedApprovalIds((prev) => new Set([...prev, ...deniedIds]))
      return sentIds
    },
    [handleSubmitApprovals]
  )

  useEffect(() => {
    onMessagesChange?.(messages)
  }, [messages, onMessagesChange])

  // Display labels for the selected-tool chips. Tools are picked in the Tools
  // popover, which builds its own list, so nothing here needs ordering.
  const toolLabels = useMemo(
    () =>
      new Map<string, string>(
        (registryActions ?? [])
          .filter((action) => isAgentToolSelectable(action.action))
          .map((action) => [
            action.action,
            action.default_title || action.action,
          ])
      ),
    [registryActions]
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
      // Changing a session's tools is a session update that needs agent:execute.
      if (
        cannotExecuteAgents ||
        areToolListsEqual(selectedToolsRef.current, next)
      ) {
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
    [cannotExecuteAgents, chat, isReadonly, queuePersistTools]
  )

  const persistMcpChainRef = useRef<Promise<void>>(Promise.resolve())
  const selectedMcpIntegrationsRef = useRef<string[]>([])
  const pendingPersistedMcpIntegrationsRef = useRef<string[] | null>(null)
  const syncedMcpChatIdRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    selectedMcpIntegrationsRef.current = selectedMcpIntegrations
  }, [selectedMcpIntegrations])

  useEffect(() => {
    const nextChatId = chat?.id
    const nextMcpIntegrations = chat?.mcp_integrations ?? []

    if (syncedMcpChatIdRef.current !== nextChatId) {
      syncedMcpChatIdRef.current = nextChatId
      pendingPersistedMcpIntegrationsRef.current = null
      selectedMcpIntegrationsRef.current = nextMcpIntegrations
      setSelectedMcpIntegrations(nextMcpIntegrations)
      return
    }

    const pendingMcpIntegrations = pendingPersistedMcpIntegrationsRef.current
    if (pendingMcpIntegrations) {
      if (areToolListsEqual(nextMcpIntegrations, pendingMcpIntegrations)) {
        pendingPersistedMcpIntegrationsRef.current = null
      } else {
        // Keep the optimistic MCP selection visible until the invalidated chat
        // query catches up, matching the selected tools behavior above.
        return
      }
    }

    if (
      !areToolListsEqual(
        selectedMcpIntegrationsRef.current,
        nextMcpIntegrations
      )
    ) {
      selectedMcpIntegrationsRef.current = nextMcpIntegrations
      setSelectedMcpIntegrations(nextMcpIntegrations)
    }
  }, [chat?.id, chat?.mcp_integrations])

  const commitSelectedMcpIntegrations = useCallback(
    (next: string[]) => {
      if (areToolListsEqual(selectedMcpIntegrationsRef.current, next)) {
        return
      }

      selectedMcpIntegrationsRef.current = next
      setSelectedMcpIntegrations(next)
      if (!chat || isReadonly) {
        pendingPersistedMcpIntegrationsRef.current = null
        return
      }
      const chatId = chat.id
      pendingPersistedMcpIntegrationsRef.current = next
      persistMcpChainRef.current = persistMcpChainRef.current
        .catch(() => undefined)
        .then(async () => {
          try {
            await updateChat({
              chatId,
              update: { mcp_integrations: next },
            })
          } catch (error) {
            if (
              pendingPersistedMcpIntegrationsRef.current &&
              areToolListsEqual(
                pendingPersistedMcpIntegrationsRef.current,
                next
              )
            ) {
              pendingPersistedMcpIntegrationsRef.current = null
            }
            toast({
              title: "Failed to update MCP integrations",
              description: parseChatError(error),
              variant: "destructive",
            })
          }
        })
    },
    [chat, isReadonly, updateChat]
  )

  const removeSelectedTool = useCallback(
    (toolName: string) => {
      commitSelectedTools(
        selectedToolsRef.current.filter((tool) => tool !== toolName)
      )
    },
    [commitSelectedTools]
  )

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const nextText = event.target.value
      // The mention layer diffs against the previous value, so it has to run
      // before the new text lands in state.
      handleMentionTextChange(
        nextText,
        event.target.selectionStart ?? nextText.length
      )
      setInput(nextText)
    },
    [handleMentionTextChange]
  )

  const handleInputFocus = useCallback(() => {
    promptTextareaFocusedRef.current = true
    setIsInputFocused(true)
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
      setIsInputFocused(false)
      dismissMentions()

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
    [dismissMentions]
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
      selectedTools.map((toolName) => ({
        value: toolName,
        label: toolLabels.get(toolName) ?? toolName,
        icon: getIcon(toolName, {
          className: "size-5 shrink-0",
        }),
      })),
    [selectedTools, toolLabels]
  )

  const baseMessages = useMemo(
    () => transformMessages(markLiveDenials(messages, sentDecisions)),
    [messages, sentDecisions]
  )
  // Keeps the same array when nothing is hidden, so downstream memos hold.
  const transformedMessages = useMemo(
    () => withoutStreamedError(baseMessages, displayedError),
    [baseMessages, displayedError]
  )

  // Reads the merged thread so a continuation folded into an earlier card
  // still keeps its working line.
  const runningLineLabel = useMemo(
    () => getRunningLineLabel(status, transformedMessages, verbosityPolicy),
    [status, transformedMessages, verbosityPolicy]
  )
  const threadBlocks = useMemo(
    () => groupThread(transformedMessages),
    [transformedMessages]
  )
  // The latest turn's undecided calls, answered from the prompt box.
  const composerApprovals = useMemo(() => {
    const lastBlock = threadBlocks.at(-1)
    if (isReadonly || lastBlock?.kind !== "turn") return []
    return lastBlock.messages
      .flatMap((message) => message.parts)
      .flatMap((part) =>
        part.type === "data-approval-request" && isApprovalCardArray(part.data)
          ? part.data
          : []
      )
      .filter(
        (approval) =>
          (!approval.status || approval.status === "pending") &&
          !sentApprovalIds.has(approval.tool_call_id)
      )
  }, [isReadonly, sentApprovalIds, threadBlocks])
  const approvalComposerContext = useMemo(
    () =>
      isReadonly
        ? null
        : {
            composerIds: new Set(
              composerApprovals.map(({ tool_call_id }) => tool_call_id)
            ),
            sentIds: sentApprovalIds,
            deniedIds: deniedApprovalIds,
          },
    [composerApprovals, deniedApprovalIds, isReadonly, sentApprovalIds]
  )
  const lastMessageId = messages.at(-1)?.id

  // Response actions go on the latest message on screen when it is the
  // assistant's. That need not be the transcript's last message: a
  // continuation whose results render in the cards that opened them shows
  // nothing of its own.
  const latestResponseIndex = useMemo(() => {
    for (let index = transformedMessages.length - 1; index >= 0; index--) {
      const { role, parts } = transformedMessages[index]
      if (parts.some(isShownPart)) {
        return role === "assistant" ? index : -1
      }
    }
    return -1
  }, [transformedMessages])

  // Retry resends the request that started the latest turn, whichever message
  // shows the action: the user's prompt, or the approval submission whose
  // continuation failed. Regenerating from a later record would submit the
  // record before it instead, and reloaded history splits one turn into
  // several assistant records, some with only tool calls, followed by system
  // records such as a compaction boundary.
  const retryMessageId = useMemo(
    () => messages.findLast(isTurnRequest)?.id,
    [messages]
  )
  // Retry starts a run, so it follows the composer's gate.
  const canRetry =
    !isWaitingForResponse &&
    !isReadonly &&
    !cannotExecuteAgents &&
    !inputDisabled
  const retryingRef = useRef(false)
  const handleRetry = useCallback(async () => {
    if (retryingRef.current) return
    retryingRef.current = true
    try {
      await retryTurn(retryMessageId)
    } catch {
      toast({
        title: "Couldn't reach Tracecat",
        description: "Retry once the connection is back.",
      })
    } finally {
      retryingRef.current = false
    }
  }, [retryTurn, retryMessageId])

  const { cancelledTurnMessageIds, interruptedToolCallIds } = useMemo(
    () => collectCancelledTurns(transformedMessages),
    [transformedMessages]
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
    if (steerComposer) {
      await submitToRunningTurn(steerComposer, message)
      return
    }
    const hasText = Boolean(message.text?.trim())

    if (!hasText || isReadonly || !modelInfo) {
      return
    }

    // A submit awaits the preset write before it sends, and the composer stays
    // enabled meanwhile. Without this a second Enter would see the optimistic
    // `selectedPresetId`, skip the write it is still waiting on, and send under
    // the preset the server has -- then the first submit would resume and send
    // again. A ref rather than state because two keystrokes in the same tick
    // would both read a stale `false`.
    if (submitInFlightRef.current) {
      // Reject rather than return: a resolved duplicate makes `submitPrompt`
      // clear attachments and blank the textarea, and the submit still running
      // reads that live value to tell the sent message from a newer draft.
      throw new SubmitAbandonedError("A submit is already in flight")
    }
    submitInFlightRef.current = true
    try {
      await submitMessage(message)
    } finally {
      submitInFlightRef.current = false
    }
  }

  /** Hand the text to the running turn; the composer keeps it unless taken. */
  const submitToRunningTurn = async (
    composer: RunningComposer,
    message: PromptInputMessage
  ) => {
    const messageText = message.text ?? ""
    if (message.files.length > 0) {
      // The running turn takes text only; keep the draft and its files.
      toast({
        title: "Attachments wait for the next message",
        description: "Send them once the agent has finished.",
      })
      throw new SubmitAbandonedError("A running turn takes text only")
    }
    if (!messageText.trim()) return
    if (submitInFlightRef.current) {
      throw new SubmitAbandonedError("A submit is already in flight")
    }
    submitInFlightRef.current = true
    try {
      if (!(await composer.submit(messageText.trim()))) {
        throw new SubmitAbandonedError("The running turn did not take it")
      }
    } finally {
      submitInFlightRef.current = false
    }
    // Keep whatever was typed while the submit was in flight.
    const live = promptTextareaRef.current?.value ?? ""
    const remaining = live.startsWith(messageText)
      ? live.slice(messageText.length)
      : live
    setInput(remaining)
    dropMentionPrefix(live.length - remaining.length)
  }

  const submitMessage = async (message: PromptInputMessage) => {
    const messageText = message.text || ""

    // An `@Agent` mention sets the session preset for the whole conversation.
    // Awaited first so the write lands before the turn is sent, and before the
    // tool and MCP persistence chains below.
    const agentMention = findAgentMention(mentions.ranges)
    if (agentMention) {
      // The mention names the agent that should answer, so nothing below may
      // fall through and run the turn under the previous one.
      if (!presetSelector) {
        // `ChatInterface` drops the selector as soon as agent add-ons stop
        // resolving -- revoked, or a refetch that failed -- while the bound
        // range survives in the composer. Nothing here can apply the mention,
        // and nothing else would tell the user, so say so before abandoning.
        toast({
          title: "Could not route to the mentioned agent",
          description:
            "Agent presets are unavailable right now. Reload and try again.",
        })
        throw new SubmitAbandonedError("Preset selector is unavailable")
      }
      if (agentMention.targetId !== presetSelector.selectedPresetId) {
        // The draft is left intact -- `handlePresetChange` has already toasted
        // the error -- so the user can retry.
        const applied = await presetSelector.onSelect(agentMention.targetId)
        if (!applied) {
          // Reject rather than return: `submitPrompt` treats a resolved submit
          // as sent and runs its cleanup, which drops any attached files even
          // though nothing left the composer. It swallows a rejection instead.
          throw new SubmitAbandonedError(
            "Failed to persist the mentioned agent preset"
          )
        }
      }
    }

    // Enter does not empty the box until the awaits here resolve, so the user
    // carries on typing onto the end of the message being sent. Only what
    // follows it is a new draft: keeping the whole value would strand the sent
    // text in the composer for the next submit to send again. `input` is this
    // closure's snapshot, so only the ref knows the live value.
    const remainingDraft = () => {
      const live = promptTextareaRef.current?.value ?? ""
      return live.startsWith(messageText)
        ? live.slice(messageText.length)
        : live
    }
    // Ranges follow the text they describe: the ones inside the prefix go with
    // it, the rest keep their bindings at their new offsets. Clearing them all
    // would unbind a mention picked while this submit was in flight, leaving
    // the surviving draft naming an agent that its own submit would not route
    // to. `dropPrefix` re-reads the ranges, since this closure's copy predates
    // any such pick.
    const clearSubmittedDraft = () => {
      const live = promptTextareaRef.current?.value ?? ""
      const remaining = remainingDraft()
      setInput(remaining)
      dropMentionPrefix(live.length - remaining.length)
    }

    // The optimistic branch blanks the composer before `onBeforeSend` resolves,
    // so the submitted text's bindings have to come off at the same moment.
    // Held here so a cancelled submit can put them back with the text.
    let submittedMentions: MentionRange[] = []

    if (onBeforeSend) {
      if (optimisticBeforeSend) {
        optimisticMessageKnownTextPartKeysRef.current =
          matchingUserTextPartKeys(messages, messageText)
        setOptimisticMessageText(messageText)
        // Cut the prefix now, while the live value still carries it. Leaving it
        // to the `clearSubmittedDraft` below would measure a zero cut against a
        // composer this branch has already blanked, and the submitted mention
        // would outlive its text as a range nothing renders -- which the next
        // send reads back, re-applying a preset the user has since cleared.
        // Cutting here rather than after the await is also what keeps a mention
        // picked while the await runs: its offsets are already relative to the
        // blanked composer, so a deferred cut would filter it out instead.
        submittedMentions = mentions.ranges
        clearSubmittedDraft()
      }

      const result = await onBeforeSend(
        messageText,
        selectedTools,
        selectedMcpIntegrations
      )
      // Only clear input if onBeforeSend succeeded (non-null)
      // If null, the action was cancelled and user keeps their draft
      if (result !== null) {
        clearSubmittedDraft()
      } else if (optimisticBeforeSend) {
        optimisticMessageKnownTextPartKeysRef.current = new Set()
        setOptimisticMessageText(null)
        // Restoring the cancelled message would clobber a newer draft.
        if (!remainingDraft()) {
          // The bindings go back with the text: a restored draft that still
          // reads `@Agent` has to route there when the user retries it.
          mentions.commitEdit({
            text: messageText,
            mentions: submittedMentions,
            caret: messageText.length,
          })
        }
      }
      // Parent will handle switching sessions and sending via pendingMessage
      return
    }

    if (!chat) {
      return
    }

    // Clear input for normal message sending
    clearSubmittedDraft()

    try {
      await persistToolsChainRef.current.catch(() => undefined)
      await persistMcpChainRef.current.catch(() => undefined)
      clearError()
      sendMessage({
        text: messageText,
        ...(message.files?.length ? { files: message.files } : {}),
      })
    } catch (error) {
      console.error("Failed to send message:", error)
    }
  }

  // The badge stands in for the hint once a preset owns the session. Unlike the
  // hint it is state rather than a discoverability cue, so focus does not gate
  // it.
  const activePresetLabel =
    presetSelector && !isReadonly && presetSelector.selectedPresetId
      ? presetSelector.label
      : null

  const promptBox = (
    <div ref={promptInputContainerRef} className={promptCenterClass}>
      {runningComposer?.panel}
      {isReadonly ? (
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline" className="px-1.5 py-0 font-normal">
            Read only
          </Badge>
          {readonlyDescription}
        </div>
      ) : null}
      <MentionPopover
        open={mentions.isOpen}
        kind={mentions.kind}
        caret={mentions.caret}
        sections={mentions.sections}
        itemCount={mentions.itemCount}
        activeIndex={mentions.activeIndex}
        isLoading={mentions.isLoading}
        locked={mentions.locked}
        hasError={mentions.hasError}
        onSelect={mentions.selectSuggestion}
      >
        <PromptInput onSubmit={handleSubmit} className={promptInputClassName}>
          {/* Workspace chat surfaces attached tools via the Tools popover, so
              the header chip row is reserved for the other chat surfaces. */}
          {toolsEnabled &&
            !isWorkspaceChat &&
            selectedToolBadges.length > 0 && (
              <SelectedToolsHeader
                badges={selectedToolBadges}
                onRemove={removeSelectedTool}
                removeDisabled={
                  isUpdatingTools ||
                  isReadonly ||
                  cannotExecuteAgents ||
                  inputDisabled ||
                  !toolsEnabled
                }
              />
            )}
          <PromptInputBody>
            <PromptInputTextarea
              ref={promptTextareaRef}
              onChange={handleInputChange}
              // The mention layer owns popover keys and atomic mention
              // backspace. It calls preventDefault when it consumes a key,
              // which is what stops the prompt input from submitting on Enter
              // while the popover is open.
              onKeyDown={handlePromptKeyDown}
              onFocus={handleInputFocus}
              onBlur={handleInputBlur}
              onSelect={mentions.handleSelectionChange}
              placeholder={composerPlaceholder}
              value={input}
              autoFocus={autoFocusInput && !isReadonly && !inputDisabled}
              disabled={isInputDisabled}
            />
          </PromptInputBody>
          <PromptInputFooter>
            <PromptInputTools>
              {toolsEnabled && !isReadonly && (
                <ChatToolsPicker
                  registryActions={registryActions ?? []}
                  selectedTools={selectedTools}
                  onToolsChange={commitSelectedTools}
                  mcpIntegrations={mcpIntegrations ?? []}
                  selectedMcpIntegrations={selectedMcpIntegrations}
                  onMcpChange={commitSelectedMcpIntegrations}
                  mcpEnabled={sessionMcpEnabled}
                  disabled={
                    inputDisabled || isUpdatingTools || cannotExecuteAgents
                  }
                  surface={surface}
                  formsEnabled={formsAccess === "enabled"}
                  mcpIntegrationsHref={`/workspaces/${workspaceId}/mcp-servers`}
                />
              )}
              {!isReadonly && modelInfo ? (
                <PromptModelIndicator modelInfo={modelInfo} />
              ) : null}
            </PromptInputTools>
            <div className="flex items-center gap-2">
              {activePresetLabel ? (
                <ActivePresetBadge
                  label={activePresetLabel}
                  showSpinner={presetSelector?.showSpinner}
                  disabled={
                    presetSelector?.disabled ||
                    inputDisabled ||
                    cannotExecuteAgents ||
                    !canSubmit
                  }
                  onClear={() => void presetSelector?.onSelect(null)}
                />
              ) : (
                <MentionHint
                  show={isInputFocused && !input.trim()}
                  agents={mentions.agents}
                  workflows={mentions.workflows}
                />
              )}
              <PromptInputSubmit
                disabled={
                  isReadonly ||
                  cannotExecuteAgents ||
                  (isGeneratingTurn
                    ? isCancellingChatTurn || cancelRequested
                    : isInputDisabled || !input.trim())
                }
                onStop={() => void handleStop()}
                status={status}
                className="size-7 text-muted-foreground/80"
              />
            </div>
          </PromptInputFooter>
        </PromptInput>
      </MentionPopover>
    </div>
  )

  const promptComposer =
    composerApprovals.length > 0 && !isGeneratingTurn ? (
      <div ref={promptInputContainerRef} className={promptCenterClass}>
        <ApprovalRequestPart
          key={composerApprovals.map((a) => a.tool_call_id).join(":")}
          variant="composer"
          approvals={composerApprovals}
          onSubmit={handleSubmitComposerApprovals}
        />
      </div>
    ) : (
      promptBox
    )

  const isEmptyConversation =
    !displayedError &&
    !optimisticMessageText &&
    !isWaitingForResponse &&
    !transformedMessages.some(messageHasVisibleParts)
  const showEmptyHero = isWorkspaceChat && !isReadonly && isEmptyConversation

  if (showEmptyHero) {
    return (
      <div className={cn("flex h-full min-h-0 flex-col", className)}>
        <div
          className="flex min-h-0 flex-1 flex-col"
          onPointerDownCapture={handlePromptPointerDownCapture}
        >
          <ChatEmptyHero>{promptComposer}</ChatEmptyHero>
        </div>
      </div>
    )
  }

  const conversationView = (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">
          <Conversation
            className="flex-1"
            resize={status === "streaming" ? "instant" : "smooth"}
          >
            <ConversationContent className={chatContentCenterClass}>
              {optimisticMessageText ? (
                <OptimisticPendingMessage text={optimisticMessageText} />
              ) : null}
              {emptyState && isEmptyConversation ? (
                <div className="flex min-h-[50vh] items-center justify-center">
                  {emptyState}
                </div>
              ) : null}
              {threadBlocks.map((block, blockIndex) => {
                if (block.kind === "prompt") {
                  const { message } = block
                  if (!messageHasVisibleParts(message)) {
                    return null
                  }
                  return (
                    <div key={message.id} className="group relative">
                      <MessageParts
                        message={message}
                        status={status}
                        isLastMessage={message.id === lastMessageId}
                        turnCancelled={cancelledTurnMessageIds.has(message.id)}
                        interruptedToolCallIds={interruptedToolCallIds}
                      />
                    </div>
                  )
                }
                // Keep the latest response's actions visible.
                const holdsLatestResponse =
                  latestResponseIndex >= block.startIndex &&
                  latestResponseIndex <= block.endIndex
                const sourceUrlParts = block.messages.flatMap((message) =>
                  message.parts.filter((part) => part.type === "source-url")
                )
                return (
                  <div
                    key={`turn-${block.startIndex}`}
                    className="group relative"
                  >
                    {verbosityPolicy.showSources &&
                      sourceUrlParts.length > 0 && (
                        <Sources className="py-1">
                          <SourcesTrigger count={sourceUrlParts.length} />
                          <SourcesContent>
                            {sourceUrlParts.map((part) => (
                              <Source
                                key={part.sourceId}
                                href={part.url}
                                title={part.title ?? part.url}
                              />
                            ))}
                          </SourcesContent>
                        </Sources>
                      )}
                    <AgentTurn
                      messages={block.messages}
                      startedAt={block.startedAt}
                      status={status}
                      isLive={
                        blockIndex === threadBlocks.length - 1 &&
                        isGeneratingTurn
                      }
                      isSuperseded={blockIndex < threadBlocks.length - 1}
                      lastMessageId={lastMessageId}
                      cancelledMessageIds={cancelledTurnMessageIds}
                      interruptedToolCallIds={interruptedToolCallIds}
                      onSubmitApprovals={
                        isReadonly ? undefined : handleSubmitApprovals
                      }
                    />
                    {/* A failed turn's Retry sits with its error below. */}
                    {holdsLatestResponse && canRetry && !displayedError && (
                      <RetryAction onRetry={handleRetry} />
                    )}
                  </div>
                )
              })}
              {displayedError && (
                <TurnErrorLine
                  error={displayedError}
                  dropped={lastErrorDisconnected}
                  // A drop reconnects, so it needs no message to resend.
                  onRetry={
                    canRetry && (lastErrorDisconnected || messages.length > 0)
                      ? handleRetry
                      : undefined
                  }
                />
              )}
              {runningLineLabel && <ThinkingLine label={runningLineLabel} />}
            </ConversationContent>
            <ConversationScrollButton />
          </Conversation>
        </div>
        <div
          className="px-3 pb-3"
          onPointerDownCapture={handlePromptPointerDownCapture}
        >
          {promptComposer}
        </div>
      </div>
    </div>
  )

  return (
    <ChatVerbosityContext.Provider value={verbosity}>
      <SubagentStreamContext.Provider value={subagentStreamContext}>
        <ApprovalComposerContext.Provider value={approvalComposerContext}>
          {conversationView}
        </ApprovalComposerContext.Provider>
      </SubagentStreamContext.Provider>
    </ChatVerbosityContext.Provider>
  )
}

type SelectedToolBadge = {
  value: string
  label: string
  icon: ReactNode
}

/** Single selected-tool chip. Renders the remove button only when interactive
 * so the hidden measurement layer stays out of the tab order. */
function SelectedToolChip({
  tool,
  onRemove,
  removeDisabled,
  interactive,
}: {
  tool: SelectedToolBadge
  onRemove?: (value: string) => void
  removeDisabled?: boolean
  interactive: boolean
}) {
  return (
    <Badge variant="secondary" className="h-7 shrink-0 gap-1.5 px-2.5 text-xs">
      <span className="inline-flex items-center justify-center text-foreground">
        {tool.icon}
      </span>
      <span className="truncate">{tool.label}</span>
      {interactive ? (
        <button
          type="button"
          className="inline-flex items-center text-muted-foreground hover:text-foreground"
          aria-label={`Remove ${tool.label}`}
          onClick={() => onRemove?.(tool.value)}
          disabled={removeDisabled}
        >
          <XIcon className="size-3.5" />
        </button>
      ) : (
        <span className="inline-flex items-center text-muted-foreground">
          <XIcon className="size-3.5" />
        </span>
      )}
    </Badge>
  )
}

/**
 * Chip strip of tools attached to the chat session, rendered above the
 * composer textarea. Keeps the chips on a single line, showing only those that
 * fit plus a "+N" indicator for the rest, so a large tool set never grows the
 * composer past one row.
 *
 * Overflow measurement is shared with MultiSelectBadges (case custom fields)
 * via the useOverflowBadges hook: a hidden measurement layer renders every chip
 * so the visible set can be recomputed when the panel resizes.
 */
function SelectedToolsHeader({
  badges,
  onRemove,
  removeDisabled,
}: {
  badges: SelectedToolBadge[]
  onRemove: (value: string) => void
  removeDisabled: boolean
}) {
  // gap-1.5 = 0.375rem = 6px between chips.
  const { measureRef, visibleCount } = useOverflowBadges(badges, { gap: 6 })

  const hiddenTools = badges.slice(visibleCount)
  const hiddenCount = hiddenTools.length

  return (
    <PromptInputHeader className="gap-1.5 px-3 pt-3">
      <div className="relative w-full min-w-0 overflow-hidden">
        {/* Hidden measurement layer — every chip plus a +N placeholder so the
            ResizeObserver can recompute the visible set as the panel resizes. */}
        <div
          ref={measureRef}
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-1.5"
          style={{ visibility: "hidden" }}
        >
          {badges.map((tool) => (
            <SelectedToolChip
              key={tool.value}
              tool={tool}
              interactive={false}
            />
          ))}
          <span className="shrink-0 text-xs">+{badges.length}</span>
        </div>
        {/* Visible layer — only the chips that fit plus a +N indicator. */}
        <div className="flex items-center gap-1.5">
          {badges.slice(0, visibleCount).map((tool) => (
            <SelectedToolChip
              key={tool.value}
              tool={tool}
              onRemove={onRemove}
              removeDisabled={removeDisabled}
              interactive
            />
          ))}
          {hiddenCount > 0 && (
            <Badge
              variant="secondary"
              className="h-7 shrink-0 px-2.5 text-xs text-muted-foreground"
              title={hiddenTools.map((tool) => tool.label).join(", ")}
            >
              +{hiddenCount}
            </Badge>
          )}
        </div>
      </div>
    </PromptInputHeader>
  )
}

function OptimisticPendingMessage({ text }: { text: string }) {
  return (
    <>
      <Message from="user">
        <MessageContent variant="flat">
          <Response>{text}</Response>
        </MessageContent>
      </Message>
      <ThinkingLine label="Thinking..." />
    </>
  )
}

/** The running line below the thread, faded in. */
function ThinkingLine({ label }: { label: string }) {
  return (
    <motion.div
      className="mt-1"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: "easeInOut" }}
    >
      <Trace>
        <RunningLine label={label} />
      </Trace>
    </motion.div>
  )
}

/**
 * The session's active agent preset, shown where the mention hint otherwise
 * sits. Clearing it is the only way back to a tools-only session.
 */
function ActivePresetBadge({
  label,
  disabled = false,
  showSpinner = false,
  onClear,
}: {
  label: string
  disabled?: boolean
  showSpinner?: boolean
  onClear: () => void
}) {
  return (
    <span className="flex h-7 min-w-0 max-w-[14rem] items-center gap-1.5 rounded-md border border-border/70 px-2 text-xs text-muted-foreground">
      <MousePointerClickIcon className="size-3 shrink-0" />
      <span className="truncate" title={label}>
        {label}
      </span>
      {showSpinner ? (
        <Loader2 className="size-3 shrink-0 animate-spin" />
      ) : (
        <button
          type="button"
          onClick={onClear}
          disabled={disabled}
          aria-label="Clear agent"
          className="shrink-0 text-muted-foreground/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
        >
          <XIcon className="size-3" />
        </button>
      )}
    </span>
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
    case "mistral":
      return "mistral"
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
      className="h-7 min-w-0 max-w-[26rem] gap-1.5 px-2.5 text-xs font-normal"
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

/**
 * Whether a part counts as response content when placing response actions.
 * The cancelled marker and approval cards count. Other data parts don't: a
 * compaction badge is a status line rather than a response, and the rest,
 * such as an approval submission, render nothing.
 */
function isShownPart(part: UIMessage["parts"][number]): boolean {
  if (part.type === "step-start") {
    return false
  }
  return (
    !part.type.startsWith("data-") ||
    part.type === CANCELLED_DATA_PART_TYPE ||
    part.type === "data-approval-request"
  )
}
