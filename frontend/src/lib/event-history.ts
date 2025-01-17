import { Buffer } from "buffer"
import { WorkflowExecutionEvent } from "@/client"

export const decode = (str: string): string =>
  Buffer.from(str, "base64").toString("binary")
export const encode = (str: string): string =>
  Buffer.from(str, "binary").toString("base64")

export const ERROR_EVENT_TYPES: WorkflowExecutionEvent["event_type"][] = [
  "WORKFLOW_EXECUTION_FAILED",
  "WORKFLOW_EXECUTION_TERMINATED",
  "WORKFLOW_EXECUTION_TIMED_OUT",
  "ACTIVITY_TASK_FAILED",
  "ACTIVITY_TASK_TIMED_OUT",
  "CHILD_WORKFLOW_EXECUTION_FAILED",
] as const
export const SUCCESS_EVENT_TYPES: WorkflowExecutionEvent["event_type"][] = [
  "ACTIVITY_TASK_COMPLETED",
  "WORKFLOW_EXECUTION_COMPLETED",
  "CHILD_WORKFLOW_EXECUTION_COMPLETED",
] as const
export const STARTED_EVENT_TYPES: WorkflowExecutionEvent["event_type"][] = [
  "ACTIVITY_TASK_STARTED",
  "WORKFLOW_EXECUTION_STARTED",
  "CHILD_WORKFLOW_EXECUTION_STARTED",
] as const

export type Input = {
  payloads: {
    metadata: { encoding: string }
    data: string // This is a base64 encoded string
  }[]
}

export type WorkflowExecutionStartedDetails = {
  workflowType: { name: string }
  input: Input
}
export type WorkflowExecutionStartedEvent = Omit<
  WorkflowExecutionEvent,
  "details"
> & {
  details: WorkflowExecutionStartedDetails
}
export type ActivityTaskScheduledEventDetails = {
  activityId: string
  activityType: { name: string }
  input: Input
  workflowTaskCompletedEventId: string
}

export type ActivityTaskStartedEvent = Omit<
  WorkflowExecutionEvent,
  "details"
> & {
  details: ActivityTaskScheduledEventDetails
}

export function parseEventType(
  eventType: WorkflowExecutionEvent["event_type"]
) {
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
export function parseExecutionId(fullExecutionId: string): [string, string] {
  // Split at most once from the left, keeping any remaining colons in the second part
  const splitIndex = fullExecutionId.indexOf(":")
  if (splitIndex === -1) {
    throw new Error("Invalid execution ID format - missing colon separator")
  }
  const workflowId = fullExecutionId.slice(0, splitIndex)
  const executionId = fullExecutionId.slice(splitIndex + 1)
  return [workflowId, executionId]
}
