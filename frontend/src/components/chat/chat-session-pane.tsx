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
  HammerIcon,
  PencilIcon,
  RefreshCcwIcon,
  XIcon,
} from "lucide-react"
import { motion } from "motion/react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
  PromptInputButton,
  type PromptInputMessage,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
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
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"
import { ChatToolsDialog } from "@/components/chat/chat-tools-dialog"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Dots } from "@/components/loading/dots"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  type ApprovalCard,
  makeContinueMessage,
  useVercelChat,
} from "@/hooks/use-chat"
import type { ModelInfo } from "@/lib/chat"
import {
  ENTITY_TO_INVALIDATION,
  toUIMessage,
  transformMessages,
} from "@/lib/chat"
import { cn } from "@/lib/utils"

export interface ChatSessionPaneProps {
  chat: AgentSessionReadVercel | ChatReadVercel
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
  onBeforeSend?: (messageText: string) => Promise<string | null>
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
  const [toolsDialogOpen, setToolsDialogOpen] = useState(false)

  // Check if this is a legacy read-only session
  const isReadonly = "is_readonly" in chat && chat.is_readonly === true

  const uiMessages = useMemo(
    () => (chat?.messages || []).map(toUIMessage),
    [chat?.messages]
  )
  const { sendMessage, messages, status, regenerate, lastError, clearError } =
    useVercelChat({
      chatId: chat.id,
      workspaceId,
      messages: uiMessages,
      modelInfo,
    })

  // Track whether we've sent the pending message to avoid double-sends
  const pendingMessageSentRef = useRef(false)

  // Send pending message on mount (used after forking)
  useEffect(() => {
    if (pendingMessage && !pendingMessageSentRef.current && !isReadonly) {
      pendingMessageSentRef.current = true
      clearError()
      sendMessage({ text: pendingMessage })
      onPendingMessageSent?.()
    }
  }, [
    pendingMessage,
    isReadonly,
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
      console.log("decisionPayload", decisionPayload)
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
      console.log("Invalidating entity queries for tools:", toolNames)
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
      const result = await onBeforeSend(messageText)
      // Only clear input if onBeforeSend succeeded (non-null)
      // If null, the action was cancelled and user keeps their draft
      if (result !== null) {
        setInput("")
      }
      // Parent will handle switching sessions and sending via pendingMessage
      return
    }

    // Clear input for normal message sending
    setInput("")

    try {
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
      <div className="px-4 pb-4">
        <PromptInput onSubmit={handleSubmit}>
          <PromptInputBody>
            <PromptInputTextarea
              onChange={(event) => setInput(event.target.value)}
              placeholder={
                isReadonly
                  ? "This is a legacy session (read-only)"
                  : placeholder
              }
              value={input}
              autoFocus={autoFocusInput && !isReadonly}
              disabled={isReadonly || !canSubmit}
            />
          </PromptInputBody>
          <PromptInputToolbar>
            {toolsEnabled && !isReadonly && (
              <PromptInputTools>
                <TooltipProvider delayDuration={0}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <PromptInputButton
                        aria-label="Configure tools"
                        size="sm"
                        onClick={() => setToolsDialogOpen(true)}
                        className="h-7 gap-1 px-2"
                        variant="ghost"
                        disabled={!!status}
                      >
                        <HammerIcon className="size-4" />
                        <span className="text-xs">Tools</span>
                      </PromptInputButton>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      Configure tools for the agent
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </PromptInputTools>
            )}
            <PromptInputSubmit
              disabled={isReadonly || !canSubmit || !input}
              status={status}
              className="ml-auto text-muted-foreground/80"
            />
          </PromptInputToolbar>
        </PromptInput>
        {toolsEnabled && !isReadonly && (
          <ChatToolsDialog
            chatId={chat.id}
            open={toolsDialogOpen}
            onOpenChange={setToolsDialogOpen}
          />
        )}
      </div>
    </div>
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
            className: "size-4 p-[3px]",
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
      <div>
        <p className="text-xs font-medium uppercase text-muted-foreground">
          Approvals required
        </p>
      </div>
      <div className="space-y-3">
        {approvals.map((approval) => {
          const actionId = approval.tool_name.replaceAll("__", ".")
          const decision = decisions[approval.tool_call_id]
          const argsPreview = formatArgs(approval.args)
          return (
            <div
              key={approval.tool_call_id}
              className="space-y-3 rounded-md border border-border/60 bg-background p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-1">
                    {getIcon(actionId, {
                      className: "size-4 p-[3px]",
                    })}
                    <p className="text-sm font-semibold">{actionId}</p>
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
                    size="sm"
                    variant={
                      decision?.action === "approve" ? "default" : "outline"
                    }
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
                        "bg-green-500/80 hover:bg-green-600/80"
                    )}
                  >
                    <CheckIcon className="mr-1 size-3" />
                    Approve
                  </Button>
                  <Button
                    size="sm"
                    variant={
                      decision?.action === "override" ? "default" : "outline"
                    }
                    disabled={disabled || submitting}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "override",
                        reason: undefined,
                      })
                    }
                    className={cn(
                      decision?.action === "override" &&
                        "bg-green-500/80 hover:bg-green-600/80"
                    )}
                  >
                    <PencilIcon className="mr-1 size-3" />
                    Approve + change
                  </Button>
                  <Button
                    size="sm"
                    variant={
                      decision?.action === "deny" ? "destructive" : "outline"
                    }
                    disabled={disabled || submitting}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "deny",
                        overrideArgs: undefined,
                      })
                    }
                  >
                    <XIcon className="mr-1 size-3" />
                    Deny
                  </Button>
                </div>
              </div>
              {decision?.action === "override" && (
                <Textarea
                  className="text-xs"
                  rows={4}
                  spellCheck={false}
                  value={decision.overrideArgs ?? ""}
                  onChange={(event) =>
                    setDecision(approval.tool_call_id, {
                      ...decision,
                      overrideArgs: event.target.value,
                    })
                  }
                  placeholder={argsPreview}
                  disabled={disabled || submitting}
                />
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
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          disabled={submitting}
          onClick={() => setDecisions({})}
          className="h-7 px-2 text-muted-foreground/80"
        >
          Reset
        </Button>
        <Button
          onClick={handleSubmit}
          disabled={disabled || submitting || !readyToSubmit}
          className="h-7 px-2"
        >
          {submitting ? "Submitting..." : "Submit"}
        </Button>
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
