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
  CircleAlertIcon,
  CircleCheckIcon,
  LoaderCircleIcon,
  MousePointerClickIcon,
  TerminalIcon,
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
  ToolInput,
  ToolOutput,
  type ToolPart,
  type ToolState,
  ToolTerminalOutput,
  type ToolVerbs,
} from "@/components/ai-elements/tool"
import { ApprovalRequestPart } from "@/components/chat/approval-request-part"
import { SmoothResponse } from "@/components/chat/smooth-response"
import { getIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  Trace,
  TraceDescription,
  TraceIcon,
  TraceLine,
  type TraceStatus,
  TraceTitle,
} from "@/components/ui/trace"
import {
  type ChatVerbosity,
  useChatVerbosity,
} from "@/hooks/use-chat-verbosity"
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

type MessagePartProps = {
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
  /** How long a reloaded thought took, from history timestamps. */
  reasoningSeconds?: number
}

type DerivedToolState = {
  state: ToolState
  errorText: unknown
  isInterrupted: boolean
}

/**
 * The display state of a tool call. Servers can encode validation feedback in
 * `output` rather than `errorText`, and a stopped turn turns pending or
 * SDK-aborted calls into interruptions rather than errors.
 */
function deriveToolState(
  part: ToolPart,
  turnCancelled: boolean,
  interruptedToolCallIds: ReadonlySet<string>
): DerivedToolState {
  const partErrorText =
    part.state === "output-error" ? part.errorText : undefined
  const errorText = partErrorText ?? asInputRecord(part.output)?.errorText
  // The backend reports aborted calls as structured metadata, which applies
  // even when the cancelled marker landed in a different message. The
  // state/error-text heuristics are a fallback for older histories and still
  // require the turn-level flag.
  const isPendingState =
    part.state === "input-streaming" || part.state === "input-available"
  const isInterrupted =
    interruptedToolCallIds.has(part.toolCallId) ||
    (turnCancelled &&
      (isPendingState ||
        (typeof errorText === "string" && isInterruptArtifactError(errorText))))
  if (isInterrupted) {
    return { state: "output-interrupted", errorText, isInterrupted }
  }
  if (errorText) {
    return { state: "output-error", errorText, isInterrupted }
  }
  return { state: part.state, errorText, isInterrupted }
}

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
  reasoningSeconds,
}: MessagePartProps) {
  const verbosity = useChatVerbosity()
  const expanded = verbosity === "verbose"

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
    const {
      label,
      icon,
      status: lineStatus,
    } = getCompactionStatus(compaction.phase)
    return (
      <Trace key={`${id}-${partIdx}`}>
        <TraceLine status={lineStatus}>
          <TraceIcon>{icon}</TraceIcon>
          <TraceTitle>{label}</TraceTitle>
          {typeof compaction.pre_tokens === "number" && (
            <TraceDescription>
              {compaction.pre_tokens.toLocaleString()} tokens
            </TraceDescription>
          )}
        </TraceLine>
      </Trace>
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
        defaultOpen={expanded}
        duration={reasoningSeconds}
        followStreaming={false}
        isStreaming={
          status === "streaming" && isLastMessage && part.state !== "done"
        }
      >
        <ReasoningTrigger />
        <ReasoningContent>{part.text}</ReasoningContent>
      </Reasoning>
    )
  }

  if (isToolUIPart(part)) {
    const toolName = getToolName(part).replaceAll("__", ".")
    const { state, errorText, isInterrupted } = deriveToolState(
      part,
      turnCancelled,
      interruptedToolCallIds
    )
    if (toolName === SUBAGENT_TOOL_NAME) {
      return (
        <SubagentToolPart
          key={`${id}-${partIdx}`}
          part={part}
          state={state}
          errorText={errorText}
          defaultOpen={expanded}
        />
      )
    }
    const display = getToolDisplay(toolName, part.input)
    const visibleErrorText =
      typeof errorText === "string" ? errorText : undefined
    let content: ReactNode
    if (isInterrupted) {
      content = (
        <div className="text-[11px] text-muted-foreground">
          Stopped before completion
        </div>
      )
    } else if (display.isCommand) {
      content = (
        <ToolTerminalOutput output={part.output} errorText={visibleErrorText} />
      )
    } else {
      content = <ToolOutput output={part.output} errorText={visibleErrorText} />
    }
    return (
      <Tool key={`${id}-${partIdx}`} defaultOpen={expanded}>
        <ToolHeader
          title={display.title}
          verbs={display.verbs}
          type={part.type}
          state={state}
          icon={display.icon}
        />
        <ToolContent>
          {display.fullCommand ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">
              {display.fullCommand}
            </pre>
          ) : null}
          {display.isCommand ? null : <ToolInput input={part.input} />}
          {content}
        </ToolContent>
      </Tool>
    )
  }

  return null
}

type MessagePartsProps = Omit<
  MessagePartProps,
  "part" | "partIdx" | "id" | "role"
> & {
  message: Pick<UIMessage, "id" | "role" | "parts">
}

type IndexedPart = { part: UIMessage["parts"][number]; partIdx: number }

type PartGroup =
  | { kind: "activity"; parts: IndexedPart[] }
  | { kind: "content"; part: IndexedPart }

/** Steps an agent takes on the way to a response, drawn as a Trace. */
export function isActivityPart(part: UIMessage["parts"][number]): boolean {
  return (
    part.type === "reasoning" ||
    part.type === "data-compaction" ||
    isToolUIPart(part)
  )
}

/** Parts that render nothing, or that the message draws elsewhere. */
export function isHiddenPart(part: UIMessage["parts"][number]): boolean {
  return (
    part.type === "step-start" ||
    part.type === "source-url" ||
    part.type === ARTIFACT_DATA_PART_TYPE
  )
}

/** Fold consecutive activity parts into runs, keeping content parts apart. */
function groupParts(parts: UIMessage["parts"]): PartGroup[] {
  const groups: PartGroup[] = []
  for (const [partIdx, part] of parts.entries()) {
    if (isHiddenPart(part)) {
      continue
    }
    if (!isActivityPart(part)) {
      groups.push({ kind: "content", part: { part, partIdx } })
      continue
    }
    const last = groups.at(-1)
    if (last?.kind === "activity") {
      last.parts.push({ part, partIdx })
    } else {
      groups.push({ kind: "activity", parts: [{ part, partIdx }] })
    }
  }
  return groups
}

/**
 * Whether a step stays visible at minimal verbosity: only failures do, so the
 * chat shows answers, approvals, and errors.
 */
function isNotableActivity(
  part: UIMessage["parts"][number],
  turnCancelled: boolean,
  interruptedToolCallIds: ReadonlySet<string>
): boolean {
  if (part.type === "data-compaction") {
    return getCompactionData(part).phase === "failed"
  }
  if (isToolUIPart(part)) {
    return (
      deriveToolState(part, turnCancelled, interruptedToolCallIds).state ===
      "output-error"
    )
  }
  return false
}

/** Harness plumbing that only the verbose level draws. */
const INTERNAL_TOOL_NAMES = new Set(["ToolSearch"])

function isInternalTool(part: UIMessage["parts"][number]): boolean {
  return isToolUIPart(part) && INTERNAL_TOOL_NAMES.has(getToolName(part))
}

function visibleActivity(
  parts: IndexedPart[],
  verbosity: ChatVerbosity,
  turnCancelled: boolean,
  interruptedToolCallIds: ReadonlySet<string>
): IndexedPart[] {
  switch (verbosity) {
    case "verbose":
      return parts
    case "default":
      return parts.filter(({ part }) => !isInternalTool(part))
    case "minimal":
      return parts.filter(({ part }) =>
        isNotableActivity(part, turnCancelled, interruptedToolCallIds)
      )
  }
}

/**
 * One message's parts. Text, approvals, and markers render in place; the
 * agent's steps between them fold into a Trace whose detail follows the
 * agent verbosity preference.
 */
export function MessageParts({
  message,
  status,
  isLastMessage,
  turnCancelled = false,
  interruptedToolCallIds = EMPTY_INTERRUPTED_TOOL_CALL_IDS,
  onSubmitApprovals,
  reasoningSeconds,
}: MessagePartsProps) {
  const verbosity = useChatVerbosity()
  const groups = useMemo(() => groupParts(message.parts), [message.parts])
  const isLive = status === "streaming" && isLastMessage

  function renderPart({ part, partIdx }: IndexedPart) {
    return (
      <MemoizedMessagePart
        key={`${message.id}-${part.type}-${partIdx}`}
        part={part}
        partIdx={partIdx}
        id={message.id}
        role={message.role}
        status={status}
        isLastMessage={isLastMessage}
        turnCancelled={turnCancelled}
        interruptedToolCallIds={interruptedToolCallIds}
        onSubmitApprovals={onSubmitApprovals}
        reasoningSeconds={reasoningSeconds}
      />
    )
  }

  return groups.map((group, groupIdx) => {
    if (group.kind === "content") {
      return renderPart(group.part)
    }
    const shown = visibleActivity(
      group.parts,
      verbosity,
      turnCancelled,
      interruptedToolCallIds
    )
    // Minimal hides the steps, so the run still in progress gets one line.
    const showWorking =
      verbosity === "minimal" && isLive && groupIdx === groups.length - 1
    if (shown.length === 0 && !showWorking) {
      return null
    }
    return (
      <Trace
        key={`${message.id}-activity-${group.parts[0].partIdx}`}
        className="py-1"
      >
        {shown.map(renderPart)}
        {showWorking ? <RunningLine label="Working..." /> : null}
      </Trace>
    )
  })
}

/** A single running line, for an agent that has not drawn anything yet. */
export function RunningLine({ label }: { label: string }) {
  return (
    <TraceLine status="running" data-testid="agent-running-line">
      <TraceTitle>{label}</TraceTitle>
    </TraceLine>
  )
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

/** Label, icon, and line status for a compaction. A missing phase means completed. */
function getCompactionStatus(phase: CompactionData["phase"]): {
  label: string
  icon: ReactNode
  status: TraceStatus | undefined
} {
  switch (phase) {
    case "started":
      return {
        label: "Compacting conversation",
        icon: <LoaderCircleIcon className="animate-spin" />,
        status: "running",
      }
    case "failed":
      return {
        label: "Compaction failed",
        icon: <CircleAlertIcon />,
        status: "error",
      }
    default:
      return {
        label: "Compacted conversation",
        icon: <CircleCheckIcon />,
        status: undefined,
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
  defaultOpen,
}: {
  part: ToolUIPart | DynamicToolUIPart
  state: ToolState
  errorText?: unknown
  defaultOpen: boolean
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
    // A running subagent opens so its steps stream in view.
    <Tool defaultOpen={defaultOpen || !finished}>
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
          <p className="line-clamp-3 whitespace-pre-wrap text-xs text-muted-foreground">
            {task}
          </p>
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
    <div className="space-y-2">
      {messages.map((message) => (
        <div key={message.id}>
          <MessageParts
            message={message}
            status={status}
            isLastMessage={message.id === lastMessageId}
            interruptedToolCallIds={interruptedToolCallIds}
          />
        </div>
      ))}
      {recoveryNotice}
    </div>
  )
}

/** Tools that read best as a verb and their target, e.g. "Read app/page.tsx". */
const TARGETED_TOOLS: Record<string, { targetKey: string; verbs: ToolVerbs }> =
  {
    Skill: {
      targetKey: "skill",
      verbs: {
        running: "Loading skill",
        done: "Loaded skill",
        failed: "Failed to load skill",
      },
    },
    Read: {
      targetKey: "file_path",
      verbs: { running: "Reading", done: "Read", failed: "Failed to read" },
    },
    Write: {
      targetKey: "file_path",
      verbs: { running: "Writing", done: "Wrote", failed: "Failed to write" },
    },
    Edit: {
      targetKey: "file_path",
      verbs: { running: "Editing", done: "Edited", failed: "Failed to edit" },
    },
    Grep: {
      targetKey: "pattern",
      verbs: {
        running: "Searching for",
        done: "Searched for",
        failed: "Failed to search for",
      },
    },
    Glob: {
      targetKey: "pattern",
      verbs: { running: "Finding", done: "Found", failed: "Failed to find" },
    },
  }

/** Past this length the line truncates, so the command shows whole below. */
const COMMAND_TITLE_CHARS = 80

type ToolDisplay = {
  title: string
  icon: ReactNode
  verbs?: ToolVerbs
  /** Shell commands show the command as the title and only its output. */
  isCommand?: boolean
  /** A command the title cuts short, shown whole above its output. */
  fullCommand?: string
}

/** How a tool line reads: commands and file tools name what they act on. */
function getToolDisplay(toolName: string, input: unknown): ToolDisplay {
  const record = asInputRecord(input)
  const command =
    toolName === "Bash" ? getNonEmptyString(record?.command) : null
  if (command) {
    const [firstLine, ...rest] = command.split("\n")
    const isCut = rest.length > 0 || firstLine.length > COMMAND_TITLE_CHARS
    return {
      title: rest.length > 0 ? `${firstLine} …` : firstLine,
      icon: <TerminalIcon />,
      isCommand: true,
      fullCommand: isCut ? command : undefined,
    }
  }
  const icon = getIcon(toolName, TOOL_ICON_PROPS)
  const targetedTool = Object.hasOwn(TARGETED_TOOLS, toolName)
    ? TARGETED_TOOLS[toolName]
    : undefined
  const target = targetedTool
    ? getNonEmptyString(record?.[targetedTool.targetKey])
    : null
  if (targetedTool && target) {
    return { title: target, icon, verbs: targetedTool.verbs }
  }
  return { title: getToolTitle(toolName, input), icon }
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
    prev.onSubmitApprovals !== next.onSubmitApprovals ||
    prev.reasoningSeconds !== next.reasoningSeconds
  ) {
    return false
  }

  const prevPart = prev.part
  const nextPart = next.part

  if (prevPart.type !== nextPart.type) {
    return false
  }

  if (prevPart.type === "text" || prevPart.type === "reasoning") {
    // A reasoning part's state ends its thinking line.
    const sameState = prevPart.state === (nextPart as typeof prevPart).state
    return prevPart.text === (nextPart as typeof prevPart).text && sameState
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
