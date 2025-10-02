"use client"
import { getToolName, isToolUIPart } from "ai"
import { CopyIcon, GlobeIcon, RefreshCcwIcon } from "lucide-react"
import { Fragment, useEffect, useState } from "react"
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
import { useCreateChat, useVercelChat } from "@/hooks/use-chat"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CaseChat({
  caseId,
  isChatOpen,
}: {
  caseId: string
  isChatOpen: boolean
}) {
  const workspaceId = useWorkspaceId()
  const { createChat } = useCreateChat(workspaceId)
  const [chatId, setChatId] = useState<string | undefined>()

  useEffect(() => {
    const fetchChat = async () => {
      return await createChat({
        title: "Case Chat",
        entity_type: "case",
        entity_id: caseId,
      })
    }
    fetchChat().then((chat) => {
      setChatId(chat.id)
    })
  }, [caseId])

  return (
    <div className="h-full flex flex-col">
      {isChatOpen && chatId && (
        <InputDemo chatId={chatId} workspaceId={workspaceId} />
      )}
    </div>
  )
}

const models = [
  { id: "gpt-4o", name: "GPT-4o" },
  { id: "claude-opus-4-20250514", name: "Claude 4 Opus" },
]

export function InputDemo({
  chatId,
  workspaceId,
}: {
  chatId: string
  workspaceId: string
}) {
  const [input, setInput] = useState<string>("")
  const [model, setModel] = useState<string>(models[0].id)
  const [_useMicrophone, _setUseMicrophone] = useState<boolean>(false)
  const [webSearch, setWebSearch] = useState<boolean>(false)

  const { sendMessage, messages, status, regenerate } = useVercelChat({
    chatId,
    workspaceId,
  })

  const handleSubmit = (message: PromptInputMessage) => {
    const hasText = Boolean(message.text)
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
            model: model,
            webSearch: webSearch,
          },
        }
      )
    } catch (error) {
      console.error("Failed to send message:", error)
    } finally {
      setInput("")
    }
  }

  console.log("messages", messages)

  return (
    <div className="max-w-4xl mx-auto p-4 relative size-full h-screen">
      <div className="flex flex-col h-full">
        <Conversation className="h-full">
          <ConversationContent>
            {messages.map((message) => (
              <div key={message.id}>
                {message.role === "assistant" &&
                  message.parts.filter((part) => part.type === "source-url")
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
                        .map((part, i) => (
                          <SourcesContent key={`${message.id}-${i}`}>
                            <Source
                              key={`${message.id}-${i}`}
                              href={part.url}
                              title={part.url}
                            />
                          </SourcesContent>
                        ))}
                    </Sources>
                  )}
                {message.parts.map((part, i) => {
                  console.debug({
                    messageId: message.id,
                    partType: part.type,
                    part: part,
                  })
                  if (part.type === "text") {
                    return (
                      <Fragment key={`${message.id}-${i}`}>
                        <Message from={message.role}>
                          <MessageContent variant="flat">
                            <Response>{part.text}</Response>
                          </MessageContent>
                        </Message>
                        {message.role === "assistant" &&
                          message.id === messages.at(-1)?.id &&
                          i === message.parts.length - 1 && (
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
                  } else if (part.type === "reasoning") {
                    return (
                      <Reasoning
                        key={`${message.id}-${i}`}
                        className="w-full"
                        isStreaming={
                          status === "streaming" &&
                          i === message.parts.length - 1 &&
                          message.id === messages.at(-1)?.id
                        }
                      >
                        <ReasoningTrigger />
                        <ReasoningContent>{part.text}</ReasoningContent>
                      </Reasoning>
                    )
                  } else if (isToolUIPart(part)) {
                    return (
                      <Tool key={`${message.id}-${i}`}>
                        <ToolHeader
                          title={getToolName(part).replaceAll("__", ".")}
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
                })}
              </div>
            ))}
            {status === "submitted" && <Dots />}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>

        <PromptInput
          onSubmit={handleSubmit}
          className="mt-4"
          globalDrop
          multiple
        >
          <PromptInputBody>
            <PromptInputAttachments>
              {(attachment) => <PromptInputAttachment data={attachment} />}
            </PromptInputAttachments>
            <PromptInputTextarea
              onChange={(e) => setInput(e.target.value)}
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
                onClick={() => setWebSearch(!webSearch)}
              >
                <GlobeIcon size={16} />
                <span>Search</span>
              </PromptInputButton>
              <PromptInputModelSelect
                onValueChange={(value) => {
                  setModel(value)
                }}
                value={model}
              >
                <PromptInputModelSelectTrigger>
                  <PromptInputModelSelectValue />
                </PromptInputModelSelectTrigger>
                <PromptInputModelSelectContent>
                  {models.map((model) => (
                    <PromptInputModelSelectItem key={model.id} value={model.id}>
                      {model.name}
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
