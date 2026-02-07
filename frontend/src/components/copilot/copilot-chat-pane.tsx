"use client"

import { useQueryClient } from "@tanstack/react-query"
import { getToolName, isToolUIPart, type UIMessage } from "ai"
import { HammerIcon, RefreshCcwIcon } from "lucide-react"
import { motion } from "motion/react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type {
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
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion"
import { MessagePart } from "@/components/chat/chat-session-pane"
import { ChatToolsDialog } from "@/components/chat/chat-tools-dialog"
import { Dots } from "@/components/loading/dots"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { makeContinueMessage, useVercelChat } from "@/hooks/use-chat"
import type { ModelInfo } from "@/lib/chat"
import {
  ENTITY_TO_INVALIDATION,
  toUIMessage,
  transformMessages,
} from "@/lib/chat"
import { cn } from "@/lib/utils"

export interface CopilotChatPaneProps {
  chat?: AgentSessionReadVercel | ChatReadVercel
  workspaceId: string
  className?: string
  placeholder?: string
  onMessagesChange?: (messages: UIMessage[]) => void
  modelInfo: ModelInfo
  toolsEnabled?: boolean
  autoFocusInput?: boolean
  suggestions?: string[]
  onBeforeSend?: (message: PromptInputMessage) => Promise<boolean>
  pendingMessage?: string | null
  onPendingMessageSent?: () => void
  inputDisabled?: boolean
}

export function CopilotChatPane({
  chat,
  workspaceId,
  className,
  placeholder = "Ask your question...",
  onMessagesChange,
  modelInfo,
  toolsEnabled = true,
  autoFocusInput = false,
  suggestions = [],
  onBeforeSend,
  pendingMessage,
  onPendingMessageSent,
  inputDisabled = false,
}: CopilotChatPaneProps) {
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
  const pendingMessageSentRef = useRef<string | null>(null)

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

  useEffect(() => {
    if (!pendingMessage || isReadonly || !chat) {
      return
    }

    const messageKey = `${chat.id}:${pendingMessage}`
    if (pendingMessageSentRef.current === messageKey) {
      return
    }

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

  const transformedMessages = useMemo(
    () => transformMessages(messages),
    [messages]
  )

  const invalidateEntityQueries = useCallback(
    (toolNames: string[]) => {
      const invalidation = ENTITY_TO_INVALIDATION["copilot"]
      if (!invalidation) return

      const { predicate, handler } = invalidation
      if (toolNames.some(predicate)) {
        handler(queryClient, workspaceId, workspaceId)
      }
    },
    [queryClient, workspaceId]
  )

  useEffect(() => {
    if (messages.length === 0) return

    const lastMessage = messages[messages.length - 1]
    const currentPartCount = lastMessage.parts?.length || 0

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
    if (!hasText) return

    if (onBeforeSend) {
      const shouldClearInput = await onBeforeSend(message)
      if (shouldClearInput) {
        setInput("")
      }
      return
    }

    if (!chat) {
      return
    }

    try {
      clearError()
      sendMessage({
        text: message.text || "Sent with attachments",
        ...(message.files?.length ? { files: message.files } : {}),
      })
    } catch (error) {
      console.error("Failed to send message:", error)
    } finally {
      setInput("")
    }
  }

  const handleSuggestionClick = (suggestion: string) => {
    setInput(suggestion)
    void handleSubmit({ text: suggestion })
  }

  const showWelcome = messages.length === 0 && !isWaitingForResponse

  const promptInputElement = (
    <PromptInput onSubmit={handleSubmit}>
      <PromptInputBody>
        <PromptInputTextarea
          onChange={(event) => setInput(event.target.value)}
          placeholder={
            isReadonly ? "This is a legacy session (read-only)" : placeholder
          }
          value={input}
          autoFocus={autoFocusInput && !isReadonly}
          disabled={isReadonly || !canSubmit || inputDisabled}
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
          disabled={isReadonly || !canSubmit || !input || inputDisabled}
          status={status}
          className="ml-auto text-muted-foreground/80"
        />
      </PromptInputToolbar>
    </PromptInput>
  )

  // Welcome state: centered layout with suggestions below input
  if (showWelcome) {
    return (
      <div className={cn("flex h-full min-h-0 flex-col", className)}>
        <div className="flex flex-1 flex-col items-center justify-center">
          <motion.div
            className="text-center"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: "easeOut" }}
          >
            <h3 className="text-lg font-medium text-foreground">
              How can I help?
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Ask about tables, cases, or anything in your workspace.
            </p>
          </motion.div>
          <motion.div
            className="mt-8 w-full max-w-2xl px-4"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.1, ease: "easeOut" }}
          >
            {promptInputElement}
          </motion.div>
          {suggestions.length > 0 && (
            <motion.div
              className="mt-4"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, delay: 0.2, ease: "easeOut" }}
            >
              <Suggestions className="justify-center">
                {suggestions.map((suggestion) => (
                  <Suggestion
                    key={suggestion}
                    suggestion={suggestion}
                    onClick={handleSuggestionClick}
                    className="text-xs"
                  >
                    {suggestion}
                  </Suggestion>
                ))}
              </Suggestions>
            </motion.div>
          )}
        </div>
        {chat && toolsEnabled && !isReadonly && (
          <ChatToolsDialog
            chatId={chat.id}
            open={toolsDialogOpen}
            onOpenChange={setToolsDialogOpen}
          />
        )}
      </div>
    )
  }

  // Conversation state: messages with input at bottom
  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex flex-1 min-h-0 flex-col">
        <Conversation className="flex-1">
          <ConversationContent>
            {lastError && (
              <Alert className="mb-4">
                <AlertTitle>Unable to continue with this model</AlertTitle>
                <AlertDescription>{lastError}</AlertDescription>
              </Alert>
            )}

            {transformedMessages.map(({ id, role, parts }) => {
              const isLastMessage = id === messages[messages.length - 1].id
              return (
                <div key={id} className="group relative">
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
                      <Actions
                        className={cn(
                          "transition-opacity duration-200 ease-out",
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
      <div className="mx-auto w-full max-w-2xl px-4 pb-4">
        {promptInputElement}
        {chat && toolsEnabled && !isReadonly && (
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
