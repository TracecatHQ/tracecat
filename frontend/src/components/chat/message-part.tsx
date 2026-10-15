"use client"

import {
  type ChatStatus,
  type DynamicToolUIPart,
  getToolName,
  isToolUIPart,
  type ToolUIPart,
  type UIDataTypes,
  type UIMessage,
  type UIMessagePart,
  type UITools,
} from "ai"
import {
  CheckIcon,
  ChevronRightIcon,
  Loader2,
  MousePointerClickIcon,
  XIcon,
} from "lucide-react"
import { memo, type ReactNode, useEffect, useMemo, useRef } from "react"
import type { ApprovalDecision } from "@/client"
import { Message, MessageContent } from "@/components/ai-elements/message"
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning"
import {
  Tool,
  ToolContent,
  ToolHeader,
  type ToolHeaderProps,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool"
import { ApprovalRequestPart } from "@/components/chat/approval-request-part"
import { SmoothResponse } from "@/components/chat/smooth-response"
import { getIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { useSubagentTranscript } from "@/hooks/use-subagent-stream"
import {
  CANCELLED_DATA_PART_TYPE,
  type CompactionData,
  getCancelledPartToolCallIds,
  isApprovalCardArray,
  isInterruptArtifactError,
} from "@/lib/chat"
import { getSubagentSessionId, SUBAGENT_TOOL_NAME } from "@/lib/subagent-stream"
import { ARTIFACT_DATA_PART_TYPE } from "@/types/workspace-chat-artifacts"

const AGENT_TOOL_NAMES = new Set(["Agent", "Task"])
const AGENT_TOOL_TARGET_KEYS = ["subagent_type", "agent_type", "type", "name"]
const AGENT_TOOL_NESTED_INPUT_KEYS = ["args", "input", "tool_input"]

const TOOL_ICON_PROPS = { className: "size-5 p-[3px]" } as const

const EMPTY_INTERRUPTED_TOOL_CALL_IDS: ReadonlySet<string> = new Set()

export function MessagePart({
  part,
  partIdx,
  id,
  role,
  status,
  isLastMessage,
  turnCancelled = false,
  interruptedToolCallIds = EMPTY_INTERRUPTED_TOOL_CALL_IDS,
  onSubmitApprovals,
}: {
  part: UIMessagePart<UIDataTypes, UITools>
  partIdx: number
  id: string
  role: UIMessage["role"]
  status?: ChatStatus
  isLastMessage: boolean
  turnCancelled?: boolean
  /**
   * Tool call IDs the backend marked as aborted by a user interrupt
   * (structured metadata from the data-cancelled part).
   */
  interruptedToolCallIds?: ReadonlySet<string>
  onSubmitApprovals?: (decisions: ApprovalDecision[]) => Promise<void>
}) {
  if (part.type === CANCELLED_DATA_PART_TYPE) {
    return (
      <div key={`${id}-${partIdx}`} className="w-full py-2">
        <span className="text-xs text-muted-foreground">Interrupted</span>
        <div className="mt-2 border-t" />
      </div>
    )
  }

  if (part.type === "data-approval-request") {
    const payload = (part as { data?: unknown }).data
    const approvals = isApprovalCardArray(payload) ? payload : []
    return (
      <ApprovalRequestPart
        key={`${id}-${partIdx}`}
        approvals={approvals}
        onSubmit={onSubmitApprovals}
      />
    )
  }

  if (part.type === "data-compaction") {
    const compaction = getCompactionData(part)
    const { label, icon } = getCompactionStatus(compaction.phase)
    return (
      <Message key={`${id}-${partIdx}`} from={role}>
        <MessageContent variant="flat">
          <div className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
            {icon}
            <span>{label}</span>
            {typeof compaction.pre_tokens === "number" && (
              <span className="text-[11px] text-muted-foreground/80">
                {compaction.pre_tokens.toLocaleString()} tokens
              </span>
            )}
          </div>
        </MessageContent>
      </Message>
    )
  }

  if (part.type === "text") {
    return (
      <Message key={`${id}-${partIdx}`} from={role}>
        <MessageContent variant="flat">
          <SmoothResponse
            text={part.text}
            animate={status === "streaming" && isLastMessage}
          />
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
    const partErrorText =
      part.state === "output-error" ? part.errorText : undefined
    const outputErrorText = asInputRecord(part.output)?.errorText
    const derivedErrorText = partErrorText ?? outputErrorText
    // In a turn the user stopped, tool calls that never completed (still
    // pending) or that only "failed" because the SDK aborted them are
    // interruptions, not tool errors — don't show a stale spinner or leak
    // internal abort messages. The backend reports aborted calls as
    // structured metadata (interruptedToolCallIds); the error-text substring
    // check is a legacy fallback for histories persisted before that
    // metadata existed.
    const isPendingState =
      part.state === "input-streaming" || part.state === "input-available"
    // The structured ids are authoritative on their own: the backend records
    // them at interrupt time, so they apply even when the cancelled marker
    // landed in a different message than the tool calls it aborted. The
    // state/error-text heuristics still require the turn-level flag.
    const isInterrupted =
      interruptedToolCallIds.has(part.toolCallId) ||
      (turnCancelled &&
        (isPendingState ||
          (typeof derivedErrorText === "string" &&
            isInterruptArtifactError(derivedErrorText))))
    let derivedState: ToolHeaderProps["state"]
    if (isInterrupted) {
      derivedState = "output-interrupted"
    } else if (derivedErrorText) {
      derivedState = "output-error"
    } else {
      derivedState = part.state
    }
    if (toolName === SUBAGENT_TOOL_NAME) {
      return (
        <SubagentToolPart
          key={`${id}-${partIdx}`}
          part={part}
          state={derivedState}
          errorText={derivedErrorText}
        />
      )
    }
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
          {isInterrupted ? (
            <div className="text-[11px] text-muted-foreground">
              Stopped before completion
            </div>
          ) : (
            <ToolOutput
              output={part.output}
              errorText={
                typeof derivedErrorText === "string"
                  ? derivedErrorText
                  : undefined
              }
            />
          )}
        </ToolContent>
      </Tool>
    )
  }

  return null
}

/** The payload of a `data-compaction` part, or an empty one if malformed. */
export function getCompactionData(
  part: UIMessage["parts"][number]
): CompactionData {
  const payload = (part as { data?: unknown }).data
  return payload && typeof payload === "object"
    ? (payload as CompactionData)
    : {}
}

/** Label and icon for a compaction badge. A missing phase means completed. */
function getCompactionStatus(phase: CompactionData["phase"]): {
  label: string
  icon: ReactNode
} {
  switch (phase) {
    case "started":
      return {
        label: "Compacting conversation...",
        icon: <Loader2 className="size-3 animate-spin" />,
      }
    case "failed":
      return { label: "Compaction failed.", icon: <XIcon className="size-3" /> }
    default:
      return {
        label: "Conversation compacted.",
        icon: <CheckIcon className="size-3" />,
      }
  }
}

function getNonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null
}

/**
 * Card for a `subagent` tool call: the child's alias, its task, a status, and
 * the child session's transcript (live while streaming, persisted once done).
 * Read-only: child approvals and input are not handled here.
 */
function SubagentToolPart({
  part,
  state,
  errorText,
}: {
  part: ToolUIPart | DynamicToolUIPart
  state: ToolHeaderProps["state"]
  errorText?: unknown
}) {
  const input = asInputRecord(part.input)
  const alias = getNonEmptyString(input?.alias) ?? "Subagent"
  const task = getNonEmptyString(input?.task)
  // Live failures wrap the child result in errorText; its output is the reason.
  const errorMessage =
    typeof errorText === "string"
      ? errorText
      : getNonEmptyString(asInputRecord(errorText)?.output)
  // A preliminary output only links the child session; the child is running.
  const isPreliminary =
    part.state === "output-available" && part.preliminary === true
  const cardState = isPreliminary ? "input-available" : state
  const finished =
    cardState !== "input-streaming" && cardState !== "input-available"

  // Error outputs may drop the session id the preliminary output carried, so
  // keep the last one seen for this card.
  const outputSessionId =
    getSubagentSessionId(part.output) ?? getSubagentSessionId(errorText)
  const lastSessionIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (outputSessionId) {
      lastSessionIdRef.current = outputSessionId
    }
  }, [outputSessionId])
  const sessionId = outputSessionId ?? lastSessionIdRef.current

  return (
    <Tool defaultOpen>
      <ToolHeader
        title={alias}
        type={part.type}
        state={cardState}
        icon={
          <MousePointerClickIcon className="size-4 text-muted-foreground" />
        }
      />
      <ToolContent>
        {task ? (
          <Collapsible>
            <CollapsibleTrigger className="group/task flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground">
              <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]/task:rotate-90" />
              Task
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-1.5">
              <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                {task}
              </p>
            </CollapsibleContent>
          </Collapsible>
        ) : null}
        <SubagentTranscript sessionId={sessionId} finished={finished} />
        {cardState === "output-interrupted" ? (
          <div className="text-[11px] text-muted-foreground">
            Stopped before completion
          </div>
        ) : null}
        {cardState === "output-error" ? (
          <ToolOutput
            output={undefined}
            errorText={errorMessage || "Subagent failed"}
          />
        ) : null}
      </ToolContent>
    </Tool>
  )
}

function SubagentTranscript({
  sessionId,
  finished,
}: {
  sessionId: string | null
  finished: boolean
}) {
  const {
    messages,
    isLive,
    isLoading,
    isError,
    isRecoveryPaused,
    retryRecovery,
  } = useSubagentTranscript({ sessionId, finished })
  // A cancelled child's own marker lists the tool calls the cancellation
  // aborted, so they render as interrupted rather than failed. Reuse the
  // shared empty set while streaming so memoized parts skip re-rendering.
  const interruptedToolCallIds = useMemo((): ReadonlySet<string> => {
    const toolCallIds = new Set<string>()
    for (const { parts } of messages) {
      for (const part of parts) {
        if (part.type === CANCELLED_DATA_PART_TYPE) {
          for (const toolCallId of getCancelledPartToolCallIds(
            (part as { data?: unknown }).data
          )) {
            toolCallIds.add(toolCallId)
          }
        }
      }
    }
    return toolCallIds.size > 0 ? toolCallIds : EMPTY_INTERRUPTED_TOOL_CALL_IDS
  }, [messages])
  const recoveryNotice = isRecoveryPaused ? (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>Transcript recovery paused.</span>
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0 text-xs"
        onClick={retryRecovery}
        aria-label="Retry transcript loading"
      >
        Retry
      </Button>
    </div>
  ) : null

  if (messages.length === 0) {
    if (isRecoveryPaused) return recoveryNotice
    let placeholder = "Waiting for the agent to start..."
    if (isError) {
      placeholder = "Could not load transcript"
    } else if (isLoading) {
      placeholder = "Loading transcript..."
    } else if (finished) {
      placeholder = "No transcript available"
    }
    return <p className="text-xs text-muted-foreground">{placeholder}</p>
  }

  const status: ChatStatus | undefined =
    isLive && !finished ? "streaming" : undefined
  const lastMessageId = messages[messages.length - 1].id
  return (
    <div className="space-y-2 border-l pl-3">
      {messages.map(({ id, role, parts }) => (
        <div key={id}>
          {parts
            .filter((part) => part.type !== ARTIFACT_DATA_PART_TYPE)
            .map((part, partIdx) => (
              <MemoizedMessagePart
                key={`${id}-${part.type}-${partIdx}`}
                part={part}
                partIdx={partIdx}
                id={id}
                role={role}
                status={status}
                isLastMessage={id === lastMessageId}
                interruptedToolCallIds={interruptedToolCallIds}
              />
            ))}
        </div>
      ))}
      {recoveryNotice}
    </div>
  )
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

export const MemoizedMessagePart = memo(MessagePart, (prev, next) => {
  if (
    prev.partIdx !== next.partIdx ||
    prev.id !== next.id ||
    prev.role !== next.role ||
    prev.status !== next.status ||
    prev.isLastMessage !== next.isLastMessage ||
    prev.turnCancelled !== next.turnCancelled ||
    prev.interruptedToolCallIds !== next.interruptedToolCallIds ||
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
