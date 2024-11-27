import { Buffer } from "buffer"
import { EventHistoryResponse } from "@/client"

export const decode = (str: string): string =>
  Buffer.from(str, "base64").toString("binary")
export const encode = (str: string): string =>
  Buffer.from(str, "binary").toString("base64")

export const ERROR_EVENT_TYPES: EventHistoryResponse["event_type"][] = [
  "WORKFLOW_EXECUTION_FAILED",
  "WORKFLOW_EXECUTION_TERMINATED",
  "WORKFLOW_EXECUTION_TIMED_OUT",
  "ACTIVITY_TASK_FAILED",
  "ACTIVITY_TASK_TIMED_OUT",
  "CHILD_WORKFLOW_EXECUTION_FAILED",
] as const
export const SUCCESS_EVENT_TYPES: EventHistoryResponse["event_type"][] = [
  "ACTIVITY_TASK_COMPLETED",
  "WORKFLOW_EXECUTION_COMPLETED",
  "CHILD_WORKFLOW_EXECUTION_COMPLETED",
] as const
export const STARTED_EVENT_TYPES: EventHistoryResponse["event_type"][] = [
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
  EventHistoryResponse,
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

export type ActivityTaskStartedEvent = Omit<EventHistoryResponse, "details"> & {
  details: ActivityTaskScheduledEventDetails
}

export function parseEventType(eventType: EventHistoryResponse["event_type"]) {
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
