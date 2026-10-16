"use client"

import { type ChatStatus, isToolUIPart, type UIMessage } from "ai"
import type { ApprovalDecision } from "@/client"
import {
  isActivityPart,
  isHiddenPart,
  MessageParts,
} from "@/components/chat/message-part"
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace"
import { useChatVerbosityPolicy } from "@/hooks/use-chat-verbosity"
import { isUserPrompt } from "@/lib/chat"

type ThreadMessage = Pick<UIMessage, "id" | "role" | "parts" | "metadata">

/** A thread split into user prompts and the agent turns that answer them. */
type ThreadBlock =
  | { kind: "prompt"; message: ThreadMessage; index: number }
  | {
      kind: "turn"
      messages: ThreadMessage[]
      /** Indexes of the turn's first and last messages in the thread. */
      startIndex: number
      endIndex: number
      /** When the prompt that started the turn was recorded. */
      startedAt: number | null
    }

/** When the runtime recorded a message, from history metadata. */
function getRecordedAt(message: ThreadMessage): number | null {
  const metadata = message.metadata
  if (
    metadata &&
    typeof metadata === "object" &&
    "created_at" in metadata &&
    typeof metadata.created_at === "string"
  ) {
    const time = Date.parse(metadata.created_at)
    return Number.isNaN(time) ? null : time
  }
  return null
}

/** When the message's last tool call returned, from history metadata. */
function getToolsCompletedAt(message: ThreadMessage): number | null {
  let latest: number | null = null
  for (const part of message.parts) {
    if (!isToolUIPart(part)) {
      continue
    }
    const completedAt = part.callProviderMetadata?.tracecat?.completed_at
    if (typeof completedAt !== "string") {
      continue
    }
    const time = Date.parse(completedAt)
    if (!Number.isNaN(time) && (latest === null || time > latest)) {
      latest = time
    }
  }
  return latest
}

/** Group the messages after each prompt into one agent turn. */
export function groupThread(messages: ThreadMessage[]): ThreadBlock[] {
  const blocks: ThreadBlock[] = []
  let startedAt: number | null = null
  for (const [index, message] of messages.entries()) {
    if (isUserPrompt(message)) {
      blocks.push({ kind: "prompt", message, index })
      startedAt = getRecordedAt(message)
      continue
    }
    const last = blocks.at(-1)
    if (last?.kind === "turn") {
      last.messages.push(message)
      last.endIndex = index
    } else {
      blocks.push({
        kind: "turn",
        messages: [message],
        startIndex: index,
        endIndex: index,
        startedAt,
      })
    }
  }
  return blocks
}

function formatDuration(ms: number): string {
  const seconds = Math.max(1, Math.round(ms / 1000))
  if (seconds < 60) {
    return `${seconds}s`
  }
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`
}

type TurnSplit = { work: ThreadMessage[]; answer: ThreadMessage[] }

/**
 * Split a finished turn at its last step: the steps and any interim text go
 * in the work, and what follows (the answer, approvals, markers) stays out.
 * Returns null when the turn ran no tools or asked for approval, so it shows
 * as is.
 */
function splitFinishedTurn(messages: ThreadMessage[]): TurnSplit | null {
  const entries = messages.flatMap((message) =>
    message.parts
      .filter((part) => !isHiddenPart(part))
      .map((part) => ({ message, part }))
  )
  // An approval card must stay in view, and the wait would inflate the time.
  if (entries.some(({ part }) => part.type === "data-approval-request")) {
    return null
  }
  const lastStep = entries.findLastIndex(({ part }) => isActivityPart(part))
  const workEntries = entries.slice(0, lastStep + 1)
  if (!workEntries.some(({ part }) => isToolUIPart(part))) {
    return null
  }
  function regroup(slice: typeof entries): ThreadMessage[] {
    const grouped: ThreadMessage[] = []
    for (const { message, part } of slice) {
      const last = grouped.at(-1)
      if (last?.id === message.id) {
        last.parts.push(part)
      } else {
        grouped.push({ ...message, parts: [part] })
      }
    }
    return grouped
  }
  return {
    work: regroup(workEntries),
    answer: regroup(entries.slice(lastStep + 1)),
  }
}

type AgentTurnProps = {
  messages: ThreadMessage[]
  startedAt: number | null
  status?: ChatStatus
  /** Whether the turn is still streaming. Live turns draw every step. */
  isLive: boolean
  lastMessageId?: string
  cancelledMessageIds?: ReadonlySet<string>
  interruptedToolCallIds?: ReadonlySet<string>
  /** Resolves with the tool call IDs whose decisions it sent. */
  onSubmitApprovals?: (decisions: ApprovalDecision[]) => Promise<string[]>
}

/**
 * The agent's answer to one prompt. Once it finishes, the steps fold into a
 * single "Worked for" line above the answer.
 */
export function AgentTurn({
  messages,
  startedAt,
  status,
  isLive,
  lastMessageId,
  cancelledMessageIds,
  interruptedToolCallIds,
  onSubmitApprovals,
}: AgentTurnProps) {
  const { foldFinishedTurns } = useChatVerbosityPolicy()
  const split =
    isLive || !foldFinishedTurns ? null : splitFinishedTurn(messages)

  // Each thought is its own history row, so it took as long as the gap since
  // the row or tool result before it.
  const reasoningSeconds = new Map<string, number>()
  let previousAt = startedAt
  for (const message of messages) {
    const recordedAt = getRecordedAt(message)
    if (
      recordedAt !== null &&
      previousAt !== null &&
      recordedAt > previousAt &&
      message.parts.some((part) => part.type === "reasoning")
    ) {
      reasoningSeconds.set(
        message.id,
        Math.max(1, Math.round((recordedAt - previousAt) / 1000))
      )
    }
    previousAt = recordedAt ?? previousAt
    const toolsCompletedAt = getToolsCompletedAt(message)
    if (
      toolsCompletedAt !== null &&
      (previousAt === null || toolsCompletedAt > previousAt)
    ) {
      previousAt = toolsCompletedAt
    }
  }

  function renderMessages(group: ThreadMessage[], keySuffix: string) {
    // Live and persisted copies of a turn can share a message id.
    return group.map((message, messageIdx) => (
      <MessageParts
        key={`${message.id}-${keySuffix}-${messageIdx}`}
        message={message}
        status={status}
        isLastMessage={message.id === lastMessageId}
        turnCancelled={cancelledMessageIds?.has(message.id)}
        interruptedToolCallIds={interruptedToolCallIds}
        onSubmitApprovals={onSubmitApprovals}
        reasoningSeconds={reasoningSeconds.get(message.id)}
      />
    ))
  }

  if (!split) {
    return renderMessages(messages, "all")
  }

  // Cancel and compaction markers carry no timestamp, and a tool's result can
  // land after every recorded row.
  const times = messages
    .flatMap((message) => [
      getRecordedAt(message),
      getToolsCompletedAt(message),
    ])
    .filter((time): time is number => time !== null)
  const endedAt = times.length > 0 ? Math.max(...times) : null
  const title =
    startedAt !== null && endedAt !== null && endedAt > startedAt
      ? `Worked for ${formatDuration(endedAt - startedAt)}`
      : "Worked"
  return (
    <>
      <Trace>
        <TraceCollapsible>
          <div className="border-b pb-2">
            <TraceCollapsibleTrigger>
              <TraceLine>
                <TraceTitle>{title}</TraceTitle>
              </TraceLine>
            </TraceCollapsibleTrigger>
          </div>
          <TraceCollapsibleContent className="ml-0 space-y-2 border-l-0 pl-0 text-foreground">
            {renderMessages(split.work, "work")}
          </TraceCollapsibleContent>
        </TraceCollapsible>
      </Trace>
      <div className="mt-2">{renderMessages(split.answer, "answer")}</div>
    </>
  )
}
