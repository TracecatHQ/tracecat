"use client"

import { useQueryClient } from "@tanstack/react-query"
import { MessageSquare } from "lucide-react"
import { motion } from "motion/react"
import Image from "next/image"
import TracecatIcon from "public/icon.png"
import { useEffect, useRef } from "react"
import { Streamdown } from "streamdown"
import type {
  BuiltinToolCallPart,
  BuiltinToolReturnPart,
  ModelRequest,
  ModelResponse,
  TextPart,
  ToolCallPart,
  UserPromptPart,
} from "@/client"
import { Dots } from "@/components/loading/dots"
import type { ModelMessage } from "@/lib/chat"

interface MessagesProps {
  messages: ModelMessage[]
  isResponding: boolean
  entityType: string
  entityId: string
  workspaceId: string
  streamingText?: string
}

const caseUpdateActions = [
  "core__cases__update_case",
  "core__cases__create_comment",
]

const runbookUpdateActions = ["core__runbooks__update_runbook"]

const assistantMarkdownStyle =
  "text-sm max-w-full text-foreground dark:prose-invert"

type AnyModelPart =
  | ModelRequest["parts"][number]
  | ModelResponse["parts"][number]

export function Messages({
  messages,
  isResponding,
  entityType,
  entityId,
  workspaceId,
  streamingText,
}: MessagesProps) {
  const queryClient = useQueryClient()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const previousMessageCountRef = useRef<number>(messages.length)
  const scrollTimeoutRef = useRef<number | null>(null)

  // Smoothly scroll when new persisted messages are added
  useEffect(() => {
    const previousCount = previousMessageCountRef.current
    if (scrollTimeoutRef.current) {
      window.clearTimeout(scrollTimeoutRef.current)
      scrollTimeoutRef.current = null
    }

    if (messages.length > previousCount) {
      scrollTimeoutRef.current = window.setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
          inline: "nearest",
        })
        scrollTimeoutRef.current = null
      }, 80)
    }

    previousMessageCountRef.current = messages.length
  }, [messages.length])

  // Keep the typing indicator in view without overlapping smooth scroll animations
  useEffect(() => {
    if (!streamingText) {
      return
    }

    if (scrollTimeoutRef.current) {
      window.clearTimeout(scrollTimeoutRef.current)
      scrollTimeoutRef.current = null
    }

    messagesEndRef.current?.scrollIntoView({
      behavior: "auto",
      block: "nearest",
      inline: "nearest",
    })
  }, [streamingText])

  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) {
        window.clearTimeout(scrollTimeoutRef.current)
        scrollTimeoutRef.current = null
      }
    }
  }, [])

  // Real-time invalidation when the agent updates a case or runbook
  // TODO: Make this generic and injectable from the parent component
  useEffect(() => {
    if (messages.length === 0) {
      return
    }

    // We have at least 1 message
    const lastMsg = messages[messages.length - 1]

    // Handle case updates (on tool-call or tool-return)
    if (
      entityType === "case" &&
      lastMsg.parts.some(
        (p) =>
          "tool_name" in p &&
          p.tool_name &&
          caseUpdateActions.includes(p.tool_name)
      )
    ) {
      console.log("Invalidating case queries")
      // Force-refetch the case & related queries so the UI updates instantly
      queryClient.invalidateQueries({ queryKey: ["case", entityId] })
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["case-events", entityId, workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case-comments", entityId, workspaceId],
      })
    }

    // Handle runbook updates (on tool-call or tool-return)
    if (
      entityType === "runbook" &&
      lastMsg.parts.some(
        (p) =>
          "tool_name" in p &&
          p.tool_name &&
          runbookUpdateActions.includes(p.tool_name)
      )
    ) {
      console.log("Invalidating runbook queries")
      // Force-refetch the runbook & related queries so the UI updates instantly
      queryClient.invalidateQueries({ queryKey: ["runbooks"], exact: false })
    }
  }, [messages, entityType, entityId, workspaceId, queryClient])

  return (
    <div className="flex flex-col min-w-0 gap-6 flex-1 overflow-y-scroll p-4 relative">
      {messages.length === 0 && <NoMessages />}
      {/* Messages */}
      {messages.map((message, index) => (
        <ChatModelMessage key={`${message.kind}-${index}`} message={message} />
      ))}
      {/* Streaming */}
      {isResponding && streamingText && (
        <motion.div
          className="flex gap-3"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.3, ease: "easeInOut" }}
        >
          <Image src={TracecatIcon} alt="Tracecat" className="size-4 mt-1" />
          <Streamdown
            className={`${assistantMarkdownStyle} flex-1`}
            parseIncompleteMarkdown
          >
            {streamingText}
          </Streamdown>
        </motion.div>
      )}
      {isResponding && !streamingText && (
        <motion.div
          className="flex gap-3 items-center"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.3, ease: "easeInOut" }}
        >
          <Image src={TracecatIcon} alt="Tracecat" className="size-4" />
          <Dots />
        </motion.div>
      )}
      <div ref={messagesEndRef} />
    </div>
  )
}

function ChatModelMessage({ message }: { message: ModelMessage }) {
  if (isModelResponse(message)) {
    return <AgentChatMessage message={message} />
  }

  if (isModelRequest(message)) {
    return <UserChatMessage message={message} />
  }

  return null
}

function AgentChatMessage({ message }: { message: ModelResponse }) {
  const textParts = message.parts.filter(isTextPart)
  const toolCalls = message.parts.filter(isToolCallPart)
  const builtinToolCalls = message.parts.filter(isBuiltinToolCallPart)
  const builtinToolReturns = message.parts.filter(isBuiltinToolReturnPart)

  const textContent = textParts
    .map((part) => part.content)
    .filter(Boolean)
    .join("\n\n")
    .trim()

  const hasToolInteractions =
    toolCalls.length > 0 ||
    builtinToolCalls.length > 0 ||
    builtinToolReturns.length > 0

  if (!textContent && !hasToolInteractions) {
    return null
  }

  return (
    <div className="flex gap-3">
      <Image src={TracecatIcon} alt="Tracecat" className="size-4 mt-1" />
      <div className="flex flex-1 flex-col gap-3 text-sm text-foreground">
        {textContent && (
          <Streamdown className={assistantMarkdownStyle}>
            {textContent}
          </Streamdown>
        )}

        {toolCalls.map((part, index) => (
          <AgentToolInteraction
            key={`tool-call-${part.tool_call_id ?? index}`}
            label="Tool call"
            toolName={part.tool_name}
            payload={part.args}
          />
        ))}

        {builtinToolCalls.map((part, index) => (
          <AgentToolInteraction
            key={`builtin-call-${part.tool_call_id ?? index}`}
            label="Built-in tool call"
            toolName={part.tool_name}
            payload={part.args}
            providerName={part.provider_name}
          />
        ))}

        {builtinToolReturns.map((part, index) => (
          <AgentToolInteraction
            key={`builtin-return-${part.tool_call_id ?? index}`}
            label="Tool result"
            toolName={part.tool_name}
            payload={part.content}
            providerName={part.provider_name}
          />
        ))}
      </div>
    </div>
  )
}

function UserChatMessage({ message }: { message: ModelRequest }) {
  const userPrompts = message.parts.filter(isUserPromptPart)

  const textContent = userPrompts
    .map((part) => normalizeUserPromptContent(part.content))
    .filter(Boolean)
    .join("\n\n")
    .trim()

  if (!textContent) {
    return null
  }

  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground shadow-sm whitespace-pre-wrap">
        {textContent}
      </div>
    </div>
  )
}

function AgentToolInteraction({
  label,
  toolName,
  payload,
  providerName,
}: {
  label: string
  toolName: string
  payload: unknown
  providerName?: string | null
}) {
  const formattedPayload = formatPayload(payload)

  return (
    <div className="rounded-lg border border-border/80 bg-muted/40 px-3 py-2 text-xs text-foreground shadow-sm">
      <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <span>{label}</span>
        <span className="rounded bg-background px-1 py-0.5 text-[11px] font-semibold normal-case text-foreground">
          {toolName}
        </span>
        {providerName && (
          <span className="text-muted-foreground/70 normal-case">
            {providerName}
          </span>
        )}
      </div>
      {formattedPayload && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted-foreground">
          {formattedPayload}
        </pre>
      )}
      {!formattedPayload && (
        <div className="mt-2 text-[11px] italic text-muted-foreground">
          No structured data returned.
        </div>
      )}
    </div>
  )
}

function normalizeUserPromptContent(
  content: UserPromptPart["content"]
): string {
  if (typeof content === "string") {
    return content
  }

  if (Array.isArray(content)) {
    return content
      .map((entry) => {
        if (typeof entry === "string") {
          return entry
        }
        if (entry && typeof entry === "object") {
          return JSON.stringify(entry)
        }
        return ""
      })
      .filter(Boolean)
      .join("\n")
  }

  if (content && typeof content === "object") {
    return JSON.stringify(content)
  }

  return ""
}

function formatPayload(value: unknown): string {
  if (value === null || value === undefined) {
    return ""
  }

  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed) {
      return ""
    }
    try {
      const parsed = JSON.parse(trimmed)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return trimmed
    }
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }

  return JSON.stringify(value, null, 2)
}

function isModelResponse(message: ModelMessage): message is ModelResponse {
  if (message.kind === "response") {
    return true
  }

  return message.parts.some((part) => {
    const kind = getPartKind(part)
    return kind ? responsePartKinds.has(kind) : false
  })
}

function isModelRequest(message: ModelMessage): message is ModelRequest {
  if (message.kind === "request") {
    return true
  }

  return message.parts.some((part) => {
    const kind = getPartKind(part)
    return kind ? requestPartKinds.has(kind) : false
  })
}

function isTextPart(part: ModelResponse["parts"][number]): part is TextPart {
  return hasPartKind(part) && part.part_kind === "text"
}

function isToolCallPart(
  part: ModelResponse["parts"][number]
): part is ToolCallPart {
  return hasPartKind(part) && part.part_kind === "tool-call"
}

function isBuiltinToolCallPart(
  part: ModelResponse["parts"][number]
): part is BuiltinToolCallPart {
  return hasPartKind(part) && part.part_kind === "builtin-tool-call"
}

function isBuiltinToolReturnPart(
  part: ModelResponse["parts"][number]
): part is BuiltinToolReturnPart {
  return hasPartKind(part) && part.part_kind === "builtin-tool-return"
}

function isUserPromptPart(
  part: ModelRequest["parts"][number]
): part is UserPromptPart {
  return hasPartKind(part) && part.part_kind === "user-prompt"
}

function hasPartKind(value: unknown): value is { part_kind?: string } {
  if (typeof value !== "object" || value === null || !("part_kind" in value)) {
    return false
  }

  const partKind = (value as { part_kind?: unknown }).part_kind
  return partKind === undefined || typeof partKind === "string"
}

function getPartKind(part: AnyModelPart): string | undefined {
  if (!hasPartKind(part)) {
    return undefined
  }

  return typeof part.part_kind === "string" ? part.part_kind : undefined
}

const responsePartKinds = new Set([
  "text",
  "tool-call",
  "builtin-tool-call",
  "builtin-tool-return",
  "thinking",
])

const requestPartKinds = new Set([
  "system-prompt",
  "user-prompt",
  "tool-return",
  "retry-prompt",
])

export function NoMessages() {
  return (
    <div className="flex h-full items-center justify-center text-center">
      <div className="max-w-sm">
        <MessageSquare className="mx-auto h-8 w-8 text-gray-400 mb-3" />
        <h4 className="text-sm font-medium text-gray-900 mb-1">
          Start a conversation
        </h4>
        <p className="text-xs text-gray-500">
          Ask me anything or get help with your tasks.
        </p>
      </div>
    </div>
  )
}
