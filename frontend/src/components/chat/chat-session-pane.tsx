"use client"

import { useQueryClient } from "@tanstack/react-query"
import { getToolName, isToolUIPart, type UIMessage } from "ai"
import { CopyIcon, GlobeIcon, RefreshCcwIcon } from "lucide-react"
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
  PromptInputActionAddAttachments,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuTrigger,
  PromptInputAttachment,
  PromptInputAttachments,
  PromptInputBody,
  PromptInputButton,
  type PromptInputMessage,
  PromptInputModelSelect,
  PromptInputModelSelectContent,
  PromptInputModelSelectItem,
  PromptInputModelSelectTrigger,
  PromptInputModelSelectValue,
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
import { Dots } from "@/components/loading/dots"
import { useVercelChat } from "@/hooks/use-chat"
import { ENTITY_TO_INVALIDATION, toUIMessage } from "@/lib/chat"
import { cn } from "@/lib/utils"

const models = [
  { id: "gpt-4o", name: "GPT-4o" },
  { id: "claude-opus-4-20250514", name: "Claude 4 Opus" },
]

export interface ChatSessionPaneProps {
  chat: ChatReadVercel
  workspaceId: string
  entityType?: ChatEntity
  entityId?: string
  className?: string
  placeholder?: string
  onMessagesChange?: (messages: UIMessage[]) => void
}

export function ChatSessionPane({
  chat,
  workspaceId,
  entityType,
  entityId,
  className,
  placeholder = "Ask your question...",
  onMessagesChange,
}: ChatSessionPaneProps) {
  const queryClient = useQueryClient()
  const processedMessageRef = useRef<string | undefined>(undefined)

  const [input, setInput] = useState<string>("")
  const [model, setModel] = useState<string>(models[0].id)
  const [webSearch, setWebSearch] = useState<boolean>(false)

  const uiMessages = useMemo(
    () => (chat?.messages || []).map(toUIMessage),
    [chat?.messages]
  )
  const { sendMessage, messages, status, regenerate } = useVercelChat({
    chatId: chat.id,
    workspaceId,
    messages: uiMessages,
  })

  useEffect(() => {
    onMessagesChange?.(messages)
  }, [messages, onMessagesChange])

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
    if (!lastMessage?.id || processedMessageRef.current === lastMessage.id) {
      return
    }

    const toolNames = (lastMessage.parts || [])
      .filter((part) => isToolUIPart(part))
      .map((part) => ("toolName" in part ? part.toolName : getToolName(part)))
      .filter((name): name is string => Boolean(name))

    if (toolNames.length > 0) {
      invalidateEntityQueries(toolNames)
    }

    processedMessageRef.current = lastMessage.id
  }, [messages, invalidateEntityQueries])

  const handleSubmit = (message: PromptInputMessage) => {
    const hasText = Boolean(message.text?.trim())
    const hasAttachments = Boolean(message.files?.length)

    if (!(hasText || hasAttachments)) {
      return
    }

    try {
      sendMessage(
        {
          text: message.text || "Sent with attachments",
          files: message.files,
        },
        {
          body: {
            model,
            webSearch,
          },
        }
      )
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
                    return (
                      <Tool key={`${message.id}-${index}`}>
                        <ToolHeader
                          title={getToolName(part)?.replaceAll("__", ".")}
                          type={part.type}
                          state={part.state}
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
        <PromptInput onSubmit={handleSubmit} globalDrop multiple>
          <PromptInputBody>
            <PromptInputAttachments>
              {(attachment) => <PromptInputAttachment data={attachment} />}
            </PromptInputAttachments>
            <PromptInputTextarea
              onChange={(event) => setInput(event.target.value)}
              placeholder={placeholder}
              value={input}
            />
          </PromptInputBody>
          <PromptInputToolbar>
            <PromptInputTools>
              <PromptInputActionMenu>
                <PromptInputActionMenuTrigger />
                <PromptInputActionMenuContent>
                  <PromptInputActionAddAttachments />
                </PromptInputActionMenuContent>
              </PromptInputActionMenu>
              <PromptInputButton
                variant={webSearch ? "default" : "ghost"}
                onClick={() => setWebSearch((previous) => !previous)}
              >
                <GlobeIcon size={16} />
                <span>Search</span>
              </PromptInputButton>
              <PromptInputModelSelect
                onValueChange={(value) => setModel(value)}
                value={model}
              >
                <PromptInputModelSelectTrigger>
                  <PromptInputModelSelectValue />
                </PromptInputModelSelectTrigger>
                <PromptInputModelSelectContent>
                  {models.map((item) => (
                    <PromptInputModelSelectItem key={item.id} value={item.id}>
                      {item.name}
                    </PromptInputModelSelectItem>
                  ))}
                </PromptInputModelSelectContent>
              </PromptInputModelSelect>
            </PromptInputTools>
            <PromptInputSubmit disabled={!input && !status} status={status} />
          </PromptInputToolbar>
        </PromptInput>
      </div>
    </div>
  )
}
