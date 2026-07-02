import type { QueryClient } from "@tanstack/react-query"
import * as ai from "ai"
import type {
  AgentSessionEntity,
  AgentSessionRead,
  AgentSessionReadVercel,
  ApprovalStatus,
  ChatReadMinimal,
  ChatReadVercel,
  UIMessage,
} from "@/client"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"

export type ApprovalCard = {
  tool_call_id: string
  tool_name: string
  args?: unknown
  status?: ApprovalStatus
  decision?: boolean | Record<string, unknown> | null
  reason?: string | null
}

type ServerToolPart = Extract<UIMessage["parts"][number], { state: string }>
type LegacyToolState =
  | "approval-requested"
  | "approval-responded"
  | "output-denied"
type ToolState = ai.ToolUIPart["state"] | LegacyToolState

/**
 * UI data part identifier for turn-cancelled markers. Streamed live at the
 * point of interruption and persisted server-side so reloads keep the marker.
 */
export const CANCELLED_DATA_PART_TYPE = "data-cancelled"

/**
 * Payload of a {@link CANCELLED_DATA_PART_TYPE} part. `tool_call_ids` is the
 * structured interrupt signal: the backend loopback records which tool calls
 * the interrupt aborted mid-flight (by cancel-then-error ordering, not error
 * text) and carries them on both the live stream event and the persisted
 * marker row.
 */
export type CancelledPartData = {
  reason?: string | null
  tool_call_ids?: string[] | null
}

/**
 * Extract the interrupted tool call IDs from a cancelled data part's payload.
 * Returns an empty array for legacy payloads that predate the field.
 */
export function getCancelledPartToolCallIds(data: unknown): string[] {
  if (!data || typeof data !== "object") {
    return []
  }
  const toolCallIds = (data as CancelledPartData).tool_call_ids
  if (!Array.isArray(toolCallIds)) {
    return []
  }
  return toolCallIds.filter((id): id is string => typeof id === "string")
}

/**
 * Error strings the Claude SDK records for tool calls that were aborted by a
 * user interrupt rather than failing on their own.
 *
 * LEGACY FALLBACK ONLY: new cancelled turns carry structured interrupt
 * metadata (`tool_call_ids` on the data-cancelled part, see
 * {@link getCancelledPartToolCallIds}) and the UI prefers that signal. This
 * substring check remains solely for histories persisted before the
 * structured metadata existed, whose markers lack `tool_call_ids`. Do not add
 * new patterns; extend the structured signal instead.
 */
const INTERRUPT_ARTIFACT_ERROR_PATTERNS = [
  "MCP error -32001",
  "The operation was aborted",
  "Request was aborted",
  "[Request interrupted by user",
  // The SDK's tool-denial phrasing: recorded when a pending approval is
  // resolved by the interrupt. New turns report these via `tool_call_ids`.
  "doesn't want to take this action",
]

/**
 * Whether a tool error text is an artifact of the user stopping the turn
 * (SDK/MCP abort plumbing) rather than a genuine tool failure. Only
 * meaningful for messages in a cancelled turn, and only consulted when the
 * turn's cancelled marker carries no structured `tool_call_ids` (legacy
 * histories).
 */
export function isInterruptArtifactError(errorText: string): boolean {
  return INTERRUPT_ARTIFACT_ERROR_PATTERNS.some((pattern) =>
    errorText.includes(pattern)
  )
}

export function isAgentSessionEntity(
  value: unknown
): value is AgentSessionEntity {
  return (
    value === "case" ||
    value === "agent_preset" ||
    value === "agent_preset_builder" ||
    value === "copilot" ||
    value === "workflow" ||
    value === "approval" ||
    value === "external_channel"
  )
}

/**
 * Either arm of a session/chat union, in list (`*Read`) or Vercel
 * (`*ReadVercel`) shape. Only the AgentSession arms carry `last_error`; the
 * legacy Chat arms do not, which is what lets `getSessionLastError` narrow.
 */
export type SessionOrChat =
  | AgentSessionRead
  | AgentSessionReadVercel
  | ChatReadMinimal
  | ChatReadVercel

/**
 * Read the persisted terminal error of a session's most recent run.
 *
 * Present iff the last run errored (errors are run-ending and clear on the next
 * turn). Lives only on the AgentSession arms of the union — legacy Chat rows
 * have no such field — so this returns null for those arms.
 */
export function getSessionLastError(session: SessionOrChat): string | null {
  return "last_error" in session ? (session.last_error ?? null) : null
}

/**
 * Type guard to check if a part is a data part.
 * DataUIPart has a `data` property and type starting with "data-"
 */
function isDataPart(
  part: UIMessage["parts"][number]
): part is Extract<UIMessage["parts"][number], { data: unknown }> {
  return "data" in part
}

/**
 * Type guard to check if a part is a tool part (has state property).
 */
function isToolPart(
  part: UIMessage["parts"][number]
): part is Extract<UIMessage["parts"][number], { state: string }> {
  return (
    "state" in part &&
    typeof (part as { state?: string }).state === "string" &&
    ((part as { state: string }).state === "input-streaming" ||
      (part as { state: string }).state === "input-available" ||
      (part as { state: string }).state === "output-available" ||
      (part as { state: string }).state === "output-error")
  )
}

/**
 * Type guard for basic UI parts (text, reasoning, file, etc.).
 */
function isBasicUIPart(
  part: UIMessage["parts"][number]
): part is Extract<
  UIMessage["parts"][number],
  | { type: "text" }
  | { type: "reasoning" }
  | { type: "file" }
  | { type: "step-start" }
  | { type: "source-url" }
  | { type: "source-document" }
> {
  return (
    part.type === "text" ||
    part.type === "reasoning" ||
    part.type === "file" ||
    part.type === "step-start" ||
    part.type === "source-url" ||
    part.type === "source-document"
  )
}

export function toUIMessage(message: UIMessage): ai.UIMessage {
  return {
    id: message.id,
    role: message.role,
    parts: message.parts.map(
      (part): ai.UIMessagePart<ai.UIDataTypes, ai.UITools> => {
        // Type-narrowing with guards ensures proper typing
        if (isDataPart(part)) {
          return part as ai.DataUIPart<ai.UIDataTypes>
        }

        if (isToolPart(part)) {
          return part as ai.ToolUIPart
        }

        if (isBasicUIPart(part)) {
          // Cast needed due to nominal type differences (e.g., providerMetadata)
          // but types are structurally compatible
          return part as ai.UIMessagePart<ai.UIDataTypes, ai.UITools>
        }

        // Exhaustiveness check: TypeScript will error if new part types are added
        const _exhaustive: never = part
        throw new Error(
          `Unhandled UI message part type: ${JSON.stringify(part)}`
        )
      }
    ),
  }
}

function normalizeToolPartForServer(
  part: ai.ToolUIPart | ai.DynamicToolUIPart
): ServerToolPart {
  const state = part.state as ToolState

  switch (state) {
    case "input-streaming":
    case "input-available":
    case "output-available":
    case "output-error":
      return part as unknown as ServerToolPart
    case "approval-requested":
    case "approval-responded": {
      const { approval: _approval, ...rest } = part as ai.DynamicToolUIPart & {
        approval?: unknown
      }
      return { ...rest, state: "input-available" } as unknown as ServerToolPart
    }
    case "output-denied": {
      const { approval, ...rest } = part as ai.DynamicToolUIPart & {
        approval?: { reason?: string | null }
      }
      return {
        ...rest,
        state: "output-error",
        errorText:
          approval?.reason?.trim() ||
          "Tool execution was denied by user approval.",
      } as unknown as ServerToolPart
    }
    default: {
      const _exhaustive: never = state
      throw new Error(
        `Unhandled tool part state in server conversion: ${JSON.stringify(_exhaustive)}`
      )
    }
  }
}

export function toServerUIMessage(message: ai.UIMessage): UIMessage {
  const parts = message.parts.map((part): UIMessage["parts"][number] => {
    if (ai.isToolUIPart(part)) {
      return normalizeToolPartForServer(part)
    }
    return part as UIMessage["parts"][number]
  })

  return {
    id: message.id,
    role: message.role,
    metadata: message.metadata,
    parts,
  }
}

const UPDATE_ON_ACTIONS: Partial<Record<AgentSessionEntity, Array<string>>> = {
  case: ["core.cases.update_case", "core.cases.create_comment"],
  agent_preset_builder: ["internal.builder.update_preset"],
}

// mapping from chatentity to
/**
 * Maps chat entity types to their query invalidation logic.
 * Each entity type defines how to invalidate related queries when updates occur.
 */
export const ENTITY_TO_INVALIDATION: Record<
  AgentSessionEntity,
  {
    predicate: (toolName: string) => boolean
    handler: (
      queryClient: QueryClient,
      workspaceId: string,
      entityId: string
    ) => void
  }
> = {
  case: {
    predicate: (toolName: string) =>
      Boolean(UPDATE_ON_ACTIONS.case?.includes(toolName)),
    handler: (queryClient, workspaceId, entityId) => {
      // Invalidate cases list for workspace
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      invalidateCaseActivityQueries(queryClient, entityId, workspaceId)
      // Invalidate case comments
      queryClient.invalidateQueries({
        queryKey: ["case-comments", entityId, workspaceId],
      })
    },
  },
  agent_preset: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for agent presets yet; placeholder for future support.
    },
  },
  agent_preset_builder: {
    predicate: (toolName: string) =>
      Boolean(UPDATE_ON_ACTIONS.agent_preset_builder?.includes(toolName)),
    handler: (queryClient, workspaceId, entityId) => {
      // Invalidate agent preset detail and workspace list
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset", workspaceId, entityId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset-versions", workspaceId, entityId],
      })
    },
  },
  copilot: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for copilot
    },
  },
  workflow: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for workflow entity
    },
  },
  approval: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for approval entity
    },
  },
  external_channel: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for external channel entity
    },
  },
}

export type ModelInfo = {
  name: string
  provider: string
  baseUrl?: string | null
  iconId?: string
}

type CompactionData = {
  phase?: "started" | "completed" | "failed"
}

/**
 * Per-toolCallId lifecycle bookkeeping accumulated in the population pass of
 * {@link transformMessages}. Positions are `"msgIndex-partIndex"` string keys.
 */
type ToolCallTrace = {
  /** Position of the dedupe winner, and whether it carries output. */
  keptPos?: string
  hasOutput: boolean
  /** Position of the input-available part that opened the call. */
  openPos?: string
  /** Position of the approval-request part matched to this call. */
  approvalPos?: string
  /** Position of the output-* part that closed the call. */
  closePos?: string
  /** Position of the most recent approval-request part naming this call. */
  lastApprovalPos?: string
}

/** Validate the custom approval data-part payload at the generated `unknown` boundary. */
export function isApprovalCardArray(data: unknown): data is ApprovalCard[] {
  return (
    Array.isArray(data) &&
    data.every((item: unknown) => {
      if (!item || typeof item !== "object") {
        return false
      }
      return (
        "tool_call_id" in item &&
        typeof item.tool_call_id === "string" &&
        item.tool_call_id.length > 0 &&
        "tool_name" in item &&
        typeof item.tool_name === "string" &&
        item.tool_name.length > 0
      )
    })
  )
}

/**
 * Concatenates all text parts from a message into a single string.
 * Skips non-text parts and preserves paragraph breaks between multiple parts.
 */
export function getAssistantText(parts: UIMessage["parts"]): string {
  if (!parts || parts.length === 0) {
    return ""
  }

  return parts.reduce<string>((accumulator, part) => {
    // Ignore non-text parts to avoid copying tool input/output.
    if (part.type !== "text") {
      return accumulator
    }

    const partText =
      "text" in part && typeof part.text === "string" ? part.text : ""

    if (partText.length === 0) {
      return accumulator
    }

    return accumulator.length > 0 ? `${accumulator}\n\n${partText}` : partText
  }, "")
}
/**
 * Transforms UI messages by managing tool call state visibility.
 *
 * Implements a state machine that tracks tool call lifecycle:
 * - input-available: Tool call initiated
 * - approval-request: Awaiting user approval
 * - output-available/output-error: Tool call completed
 *
 * Visibility rules:
 * - Streaming tool inputs remain visible
 * - Completed tool calls collapse their input and approval parts
 * - Pending approvals (without output) remain visible
 * - Duplicate DB/stream seam copies render once: latest output wins, otherwise
 *   the latest occurrence wins
 *
 * @param messages - Array of UI messages to transform
 * @returns Transformed messages with appropriate parts hidden/visible
 */
export function transformMessages(messages: ai.UIMessage[]): ai.UIMessage[] {
  // One lifecycle record per toolCallId; positions are "msgIndex-partIndex".
  const traces = new Map<string, ToolCallTrace>()
  // Array positions to ignore (using "msgIndex-partIndex" string format)
  const ignorePos = new Set<string>()
  let pendingCompactionStartPos: string | null = null

  const getTrace = (toolCallId: string): ToolCallTrace => {
    let trace = traces.get(toolCallId)
    if (!trace) {
      trace = { hasOutput: false }
      traces.set(toolCallId, trace)
    }
    return trace
  }

  // Dedupe DB/stream seam copies: an output-bearing copy supersedes any kept
  // copy; a non-output copy is ignored when an output copy is kept; otherwise
  // the latest occurrence wins. Ignored positions accumulate in `ignorePos`.
  const recordDedupe = (
    trace: ToolCallTrace,
    posKey: string,
    hasOutput: boolean
  ) => {
    if (hasOutput) {
      if (trace.keptPos) ignorePos.add(trace.keptPos)
      trace.keptPos = posKey
      trace.hasOutput = true
    } else if (trace.hasOutput) {
      ignorePos.add(posKey)
    } else {
      if (trace.keptPos) ignorePos.add(trace.keptPos)
      trace.keptPos = posKey
      trace.hasOutput = false
    }
  }

  for (const [i, message] of messages.entries()) {
    for (const [j, part] of message.parts.entries()) {
      const posKey = `${i}-${j}`

      if (ai.isToolUIPart(part)) {
        const { state, toolCallId } = part
        const hasOutput =
          state === "output-available" || state === "output-error"
        const trace = getTrace(toolCallId)
        recordDedupe(trace, posKey, hasOutput)

        if (
          !trace.hasOutput &&
          (state === "input-streaming" || state === "input-available")
        ) {
          // OPEN STATE
          // If we encounter an input part, we open a tool call state.
          // A fresh open supersedes any prior open/approval bookkeeping.
          trace.openPos = posKey
          trace.approvalPos = undefined
          trace.closePos = undefined
        } else if (hasOutput) {
          // CLOSE STATE
          // If we encounter an output-* part:
          // 1. Close the tool call state by hiding the input-* + approval parts
          // 2. Merge the input args into the output part
          if (trace.openPos) {
            ignorePos.add(trace.openPos) // Hide open state
          }
          if (trace.approvalPos) {
            ignorePos.add(trace.approvalPos) // Hide approval state
          }
          trace.closePos = posKey
        }
      } else if (part.type === "data-approval-request") {
        // Handle approval request parts
        // 1. If approval request we mark positions, only ignore if we hit a close state
        // 2. If we see approval requests after a close state, we should ignore the approval requests
        const approvals = isApprovalCardArray(part.data) ? part.data : []
        for (const approval of approvals) {
          const toolCallId = approval.tool_call_id
          const trace = getTrace(toolCallId)
          trace.lastApprovalPos = posKey
          // For each approval find the matching open state and update the approval state
          if (trace.openPos || trace.approvalPos || trace.closePos) {
            // If we already have a close state, ignore this approval request
            if (trace.closePos) {
              ignorePos.add(posKey)
            }
            trace.approvalPos = posKey
          }
        }
      } else if (part.type === "data-compaction") {
        const phase =
          part.data &&
          typeof part.data === "object" &&
          "phase" in part.data &&
          (part.data as CompactionData).phase

        if (phase === "started") {
          if (pendingCompactionStartPos) {
            ignorePos.add(pendingCompactionStartPos)
          }
          pendingCompactionStartPos = posKey
        } else if (phase === "completed" || phase === "failed") {
          if (pendingCompactionStartPos) {
            ignorePos.add(pendingCompactionStartPos)
            pendingCompactionStartPos = null
          }
        }
      }
    }
  }

  // Finally walk through each message and filter out the ignored positions
  const finalMessages: ai.UIMessage[] = []
  for (const [i, message] of messages.entries()) {
    const newParts: ai.UIMessagePart<ai.UIDataTypes, ai.UITools>[] = []
    for (const [j, part] of message.parts.entries()) {
      const posKey = `${i}-${j}`
      if (ignorePos.has(posKey)) continue
      if (
        part.type === "data-approval-request" &&
        isApprovalCardArray(part.data)
      ) {
        const remaining = part.data.filter(
          ({ tool_call_id: toolCallId }) =>
            !traces.get(toolCallId)?.hasOutput &&
            traces.get(toolCallId)?.lastApprovalPos === posKey
        )
        if (remaining.length === 0) {
          continue
        }
        if (remaining.length !== part.data.length) {
          newParts.push({ ...part, data: remaining })
          continue
        }
      }
      // Merge input from open state into output parts
      if (
        ai.isToolUIPart(part) &&
        (part.state === "output-available" || part.state === "output-error")
      ) {
        // Handle output parts
        const { toolCallId } = part
        const trace = traces.get(toolCallId)
        const newPart: Extract<
          ai.UIMessagePart<ai.UIDataTypes, ai.UITools>,
          { state: "output-available" | "output-error" }
        > = {
          ...part,
        }
        if (trace?.openPos) {
          // Extract the open position from the string key
          const [openMsgIdx, openPartIdx] = trace.openPos.split("-").map(Number)
          const openPart = messages[openMsgIdx].parts[openPartIdx]
          if (!ai.isToolUIPart(openPart)) {
            throw new Error(
              `Open part is not a tool part: ${JSON.stringify(openPart)}`
            )
          }
          newPart.input = openPart.input
        }
        newParts.push(newPart)
      } else {
        newParts.push(part)
      }
    }
    if (newParts.length > 0) {
      finalMessages.push({ ...message, parts: newParts })
    }
  }
  return finalMessages
}
