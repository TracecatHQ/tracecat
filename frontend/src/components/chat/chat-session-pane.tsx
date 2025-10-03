"use client"

import { useQueryClient } from "@tanstack/react-query"
import { getToolName, isToolUIPart, type UIMessage } from "ai"
import { CopyIcon, HammerIcon, RefreshCcwIcon } from "lucide-react"
import { motion } from "motion/react"
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type { ChatEntity, ChatReadVercel } from "@/client"
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
import { Dots } from "@/components/loading/dots"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useVercelChat } from "@/hooks/use-chat"
import type { ModelInfo } from "@/lib/chat"
import { ENTITY_TO_INVALIDATION, toUIMessage } from "@/lib/chat"
import { cn } from "@/lib/utils"

export interface ChatSessionPaneProps {
  chat: ChatReadVercel
  workspaceId: string
  entityType?: ChatEntity
  entityId?: string
  className?: string
  placeholder?: string
  onMessagesChange?: (messages: UIMessage[]) => void
  modelInfo: ModelInfo
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
  const toolsDisabled = entityType === "runbook"

  const uiMessages = useMemo(
    () => (chat?.messages || []).map(toUIMessage),
    [chat?.messages]
  )
  const { sendMessage, messages, status, regenerate } = useVercelChat({
    chatId: chat.id,
    workspaceId,
    messages: uiMessages,
    modelInfo,
  })

  useEffect(() => {
    onMessagesChange?.(messages)
  }, [messages, onMessagesChange])

  useEffect(() => {
    if (toolsDisabled && toolsDialogOpen) {
      setToolsDialogOpen(false)
    }
  }, [toolsDisabled, toolsDialogOpen])

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

  const handleSubmit = (message: PromptInputMessage) => {
    const hasText = Boolean(message.text?.trim())

    if (!hasText) {
      return
    }

    try {
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

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex flex-1 min-h-0 flex-col">
        <Conversation className="flex-1">
          <ConversationContent className="px-6">
            {messages.map((message) => (
              <div key={message.id}>
                {message.role === "assistant" &&
                  message.parts?.filter((part) => part.type === "source-url")
                    .length > 0 && (
                    <Sources>
                      <SourcesTrigger
                        count={
                          message.parts.filter(
                            (part) => part.type === "source-url"
                          ).length
                        }
                      />
                      {message.parts
                        .filter((part) => part.type === "source-url")
                        .map((part, index) => (
                          <SourcesContent key={`${message.id}-${index}`}>
                            <Source
                              href={"url" in part ? part.url : "#"}
                              title={"url" in part ? part.url : "Source"}
                            />
                          </SourcesContent>
                        ))}
                    </Sources>
                  )}

                {message.parts?.map((part, index) => {
                  if (part.type === "text") {
                    return (
                      <Fragment key={`${message.id}-${index}`}>
                        <Message from={message.role}>
                          <MessageContent variant="flat">
                            <Response>{part.text}</Response>
                          </MessageContent>
                        </Message>
                        {message.role === "assistant" &&
                          message.id === messages.at(-1)?.id &&
                          index === (message.parts?.length || 0) - 1 && (
                            <Actions>
                              <Action
                                onClick={() => regenerate()}
                                label="Retry"
                              >
                                <RefreshCcwIcon className="size-3" />
                              </Action>
                              <Action
                                onClick={() =>
                                  navigator.clipboard.writeText(part.text)
                                }
                                label="Copy"
                              >
                                <CopyIcon className="size-3" />
                              </Action>
                            </Actions>
                          )}
                      </Fragment>
                    )
                  }

                  if (part.type === "reasoning") {
                    const isLatestMessage =
                      status === "streaming" &&
                      index === (message.parts?.length || 0) - 1 &&
                      message.id === messages.at(-1)?.id

                    return (
                      <Reasoning
                        key={`${message.id}-${index}`}
                        className="w-full"
                        isStreaming={isLatestMessage}
                      >
                        <ReasoningTrigger />
                        <ReasoningContent>{part.text}</ReasoningContent>
                      </Reasoning>
                    )
                  }

                  if (isToolUIPart(part)) {
                    const toolName = getToolName(part)?.replaceAll("__", ".")
                    return (
                      <Tool key={`${message.id}-${index}`}>
                        <ToolHeader
                          title={toolName}
                          type={part.type}
                          state={part.state}
                          icon={getIcon(toolName, {
                            className: "size-4 p-[3px]",
                          })}
                        />
                        <ToolContent>
                          <ToolInput input={part.input} />
                          <ToolOutput
                            output={part.output}
                            errorText={part.errorText}
                          />
                        </ToolContent>
                      </Tool>
                    )
                  }

                  return null
                })}
              </div>
            ))}
            {status === "submitted" && (
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
              placeholder={placeholder}
              value={input}
            />
          </PromptInputBody>
          <PromptInputToolbar>
            <PromptInputTools>
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <PromptInputButton
                      aria-label="Configure tools"
                      disabled={toolsDisabled}
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
                    {toolsDisabled
                      ? "Tools are unavailable for runbooks"
                      : "Configure tools for the agent"}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </PromptInputTools>
            <PromptInputSubmit
              disabled={!input && !status}
              status={status}
              className="text-muted-foreground/80"
            />
          </PromptInputToolbar>
        </PromptInput>
        <ChatToolsDialog
          chatId={chat.id}
          open={toolsDialogOpen}
          onOpenChange={setToolsDialogOpen}
        />
      </div>
    </div>
  )
}
