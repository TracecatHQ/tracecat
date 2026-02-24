import type {
  AgentOutput,
  DSLRunArgs,
  InteractionInput,
  RunActionInput,
  WorkflowEventType,
  WorkflowExecutionEventCompact_Any__Union_AgentOutput__Any___Any_,
  WorkflowExecutionReadCompact_Any__Union_AgentOutput__Any___Any_,
} from "@/client"
import { ACTION_REF_DELIMITER, undoSlugify } from "@/lib/utils"

export type WorkflowExecutionEventCompact =
  WorkflowExecutionEventCompact_Any__Union_AgentOutput__Any___Any_

export type WorkflowExecutionReadCompact =
  WorkflowExecutionReadCompact_Any__Union_AgentOutput__Any___Any_

// Safe because refs are slugified. Use `workflow` to namespace from regular action refs.
export const WF_FAILURE_EVENT_REF = "__workflow_failure__"
export const WF_FAILURE_EVENT_LABEL = "Workflow Failure"
export const WF_COMPLETED_EVENT_REF = "__workflow_completed__"
export const WF_COMPLETED_EVENT_LABEL = "Workflow result"

export const ERROR_EVENT_TYPES: WorkflowEventType[] = [
  "WORKFLOW_EXECUTION_FAILED",
  "WORKFLOW_EXECUTION_TERMINATED",
  "WORKFLOW_EXECUTION_TIMED_OUT",
  "ACTIVITY_TASK_FAILED",
  "ACTIVITY_TASK_TIMED_OUT",
  "CHILD_WORKFLOW_EXECUTION_FAILED",
] as const
export const SUCCESS_EVENT_TYPES: WorkflowEventType[] = [
  "ACTIVITY_TASK_COMPLETED",
  "WORKFLOW_EXECUTION_COMPLETED",
  "CHILD_WORKFLOW_EXECUTION_COMPLETED",
] as const
export const STARTED_EVENT_TYPES: WorkflowEventType[] = [
  "ACTIVITY_TASK_STARTED",
  "WORKFLOW_EXECUTION_STARTED",
  "CHILD_WORKFLOW_EXECUTION_STARTED",
] as const

export function parseEventType(eventType: WorkflowEventType) {
  return eventType
    .toString()
    .split("_")
    .map((s) => s.charAt(0).toUpperCase() + s.toLowerCase().slice(1))
    .join(" ")
}

export function getRelativeTime(date: Date) {
  const now = new Date().getTime()
  const timestamp = date.getTime()
  const difference = now - timestamp

  const seconds = Math.floor(difference / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)
  const months = Math.floor(days / 30)
  const years = Math.floor(months / 12)

  if (years > 0) return `about ${years} year${years > 1 ? "s" : ""} ago`
  if (months > 0) return `about ${months} month${months > 1 ? "s" : ""} ago`
  if (days > 0) return `about ${days} day${days > 1 ? "s" : ""} ago`
  if (hours > 0) return `about ${hours} hour${hours > 1 ? "s" : ""} ago`
  if (minutes > 0) return `about ${minutes} minute${minutes > 1 ? "s" : ""} ago`
  if (seconds > 0) return `${seconds} second${seconds > 1 ? "s" : ""} ago`
  return "just now"
}

/**
 * Get the execution ID from a full execution ID
 * @param fullExecutionId
 * @returns the execution ID
 *
 * Example:
 * - "wf-123:1234567890" -> ["wf-123", "1234567890"]
 * - "wf-123:1234567890:1" -> ["wf-123", "1234567890:1"]
 */
/**
 * Parses a full execution ID into workflow ID and execution ID components
 * @param fullExecutionId - Full execution ID string in format "workflowId:executionId" or "workflowId/executionId"
 * @returns Tuple of [workflowId, executionId]
 * @throws Error if execution ID format is invalid
 */
export function parseExecutionId(fullExecutionId: string): [string, string] {
  const separators = ["/", ":"]
  for (const separator of separators) {
    const splitIndex = fullExecutionId.indexOf(separator)
    if (splitIndex !== -1) {
      return [
        fullExecutionId.slice(0, splitIndex),
        fullExecutionId.slice(splitIndex + 1),
      ]
    }
  }
  throw new Error("Invalid execution ID format - missing separator (: or /)")
}

/**
 * Formats and URL encodes a workflow execution ID
 * @param workflowId - ID of the workflow
 * @param executionId - ID of the execution
 * @returns URL encoded execution ID in format "workflowId/executionId"
 */
export function formatExecutionId(
  workflowId: string,
  executionId: string,
  separator: string = "/"
): string {
  const encodedSeparator = encodeURIComponent(separator)
  return `${workflowId}${encodedSeparator}${executionId}`
}

export type ExecutionId = {
  wf: string
  exec: string
}

export function executionId(fullExecutionId: string): ExecutionId {
  const [wf, exec] = parseExecutionId(fullExecutionId)
  return { wf, exec }
}

export function isRunActionInput(
  actionInput: unknown
): actionInput is RunActionInput {
  return (
    typeof actionInput === "object" &&
    actionInput !== null &&
    "task" in actionInput &&
    typeof (actionInput as RunActionInput).task === "object"
  )
}

export function isInteractionInput(
  actionInput: unknown
): actionInput is InteractionInput {
  return (
    typeof actionInput === "object" &&
    actionInput !== null &&
    "interaction_id" in actionInput &&
    typeof (actionInput as InteractionInput).interaction_id === "string"
  )
}

export function isDSLRunArgs(actionInput: unknown): actionInput is DSLRunArgs {
  // Define the conditions to check for DSLRunArgs
  return (
    typeof actionInput === "object" &&
    actionInput !== null &&
    // Check specific properties of DSLRunArgs
    typeof (actionInput as DSLRunArgs).dsl === "object" &&
    (actionInput as DSLRunArgs).wf_id !== undefined
  )
}

export function getWorkflowExecutionUrl(
  baseUrl: string,
  workspaceId: string,
  wf: string,
  exec: string
) {
  return `${baseUrl}/workspaces/${encodeURIComponent(workspaceId)}/workflows/${encodeURIComponent(wf)}/executions/${encodeURIComponent(exec)}`
}

export function groupEventsByActionRef(
  events: WorkflowExecutionEventCompact[]
) {
  return events.reduce(
    (acc, event) => {
      const ref = event.action_ref
      if (!acc[ref]) {
        acc[ref] = []
      }
      acc[ref].push(event)
      return acc
    },
    {} as Record<string, WorkflowExecutionEventCompact[]>
  )
}

function unwrapLoopActionResult(actionResult: unknown): unknown {
  let value = actionResult

  // Loop action results can appear in multiple envelope shapes depending on
  // source (raw materialized value vs TaskResult/StoredObject wrappers).
  for (let i = 0; i < 4; i++) {
    if (typeof value !== "object" || value === null) {
      return value
    }

    const candidate = value as Record<string, unknown>
    if ("result" in candidate) {
      value = candidate.result
      continue
    }
    if ("data" in candidate) {
      value = candidate.data
      continue
    }
    return value
  }

  return value
}

export function getLoopEventMeta(
  event?: WorkflowExecutionEventCompact
): string | undefined {
  if (!event) {
    return undefined
  }

  const compactLoop = event as {
    while_iteration?: unknown
    while_continue?: unknown
  }

  if (event.action_name === "core.loop.start") {
    if (typeof compactLoop.while_iteration === "number") {
      return `${compactLoop.while_iteration}`
    }
  }

  if (event.action_name === "core.loop.end") {
    if (typeof compactLoop.while_continue === "boolean") {
      return compactLoop.while_continue ? "continue" : "exit"
    }
  }

  const payload = unwrapLoopActionResult(event.action_result)
  if (typeof payload !== "object" || payload === null) {
    return undefined
  }

  if (event.action_name === "core.loop.start") {
    const iteration = (payload as { iteration?: unknown }).iteration
    if (typeof iteration === "number") {
      return `${iteration}`
    }
    return "?"
  }

  if (event.action_name === "core.loop.end") {
    const shouldContinue = (payload as { continue?: unknown }).continue
    if (typeof shouldContinue === "boolean") {
      return shouldContinue ? "continue" : "exit"
    }
    return "exit"
  }

  return undefined
}

function getCompactEventTimestamp(
  event: WorkflowExecutionEventCompact
): number {
  const time = event.close_time || event.start_time || event.schedule_time
  return new Date(time).getTime()
}

export function getLatestLoopEventMeta(
  events: WorkflowExecutionEventCompact[]
): string | undefined {
  const sorted = [...events].sort(
    (a, b) => getCompactEventTimestamp(b) - getCompactEventTimestamp(a)
  )
  for (const event of sorted) {
    const meta = getLoopEventMeta(event)
    if (meta) {
      return meta
    }
  }
  return undefined
}

export function parseStreamId(streamId: string): {
  scope: string
  index: string
}[] {
  const streamIdParts = streamId.split("/").map((part) => {
    const [scope, index] = part.split(":")
    return { scope, index }
  })
  return streamIdParts
}

export function formatStreamId(streamId: string) {
  const streamIdParts = parseStreamId(streamId)
  const lastPart = streamIdParts[streamIdParts.length - 1]
  if (lastPart.scope === "<root>" && lastPart.index === "0") {
    return "global scope"
  }
  return `execution stream ${lastPart.scope}[${lastPart.index}]`
}

export function isAgentOutput(
  actionResult: WorkflowExecutionEventCompact["action_result"]
): actionResult is AgentOutput {
  return (
    typeof actionResult === "object" &&
    actionResult !== null &&
    Array.isArray((actionResult as AgentOutput).message_history)
  )
}

/**
 * Map an action ref to its display label, handling special platform refs
 * @param actionRef - The action reference string to convert to a display label
 * @returns The formatted display label for the action ref
 */
export function refToLabel(actionRef: string) {
  if (actionRef === WF_FAILURE_EVENT_REF) {
    return WF_FAILURE_EVENT_LABEL
  }
  if (actionRef === WF_COMPLETED_EVENT_REF) {
    return WF_COMPLETED_EVENT_LABEL
  }
  return undoSlugify(actionRef, ACTION_REF_DELIMITER)
}
