import type { CaseEventType } from "@/client"
import type { Suggestion } from "@/components/tags-input"

export const CASE_EVENT_SUGGESTIONS: Suggestion[] = [
  {
    id: "case_created",
    label: "Case created",
    value: "case_created",
    group: "Case",
  },
  {
    id: "case_updated",
    label: "Case updated",
    value: "case_updated",
    group: "Case",
  },
  {
    id: "case_closed",
    label: "Case closed",
    value: "case_closed",
    group: "Case",
  },
  {
    id: "case_reopened",
    label: "Case reopened",
    value: "case_reopened",
    group: "Case",
  },
  {
    id: "case_viewed",
    label: "Case viewed",
    value: "case_viewed",
    group: "Case",
  },
  {
    id: "status_changed",
    label: "Status changed",
    value: "status_changed",
    group: "Fields",
  },
  {
    id: "priority_changed",
    label: "Priority changed",
    value: "priority_changed",
    group: "Fields",
  },
  {
    id: "severity_changed",
    label: "Severity changed",
    value: "severity_changed",
    group: "Fields",
  },
  {
    id: "fields_changed",
    label: "Fields changed",
    value: "fields_changed",
    group: "Fields",
  },
  {
    id: "assignee_changed",
    label: "Assignee changed",
    value: "assignee_changed",
    group: "Fields",
  },
  {
    id: "payload_changed",
    label: "Payload changed",
    value: "payload_changed",
    group: "Fields",
  },
  {
    id: "attachment_created",
    label: "Attachment added",
    value: "attachment_created",
    group: "Attachments",
  },
  {
    id: "attachment_deleted",
    label: "Attachment removed",
    value: "attachment_deleted",
    group: "Attachments",
  },
  {
    id: "tag_added",
    label: "Tag added",
    value: "tag_added",
    group: "Tags",
  },
  {
    id: "tag_removed",
    label: "Tag removed",
    value: "tag_removed",
    group: "Tags",
  },
  {
    id: "task_created",
    label: "Task created",
    value: "task_created",
    group: "Tasks",
  },
  {
    id: "task_deleted",
    label: "Task deleted",
    value: "task_deleted",
    group: "Tasks",
  },
  {
    id: "task_status_changed",
    label: "Task status changed",
    value: "task_status_changed",
    group: "Tasks",
  },
  {
    id: "task_priority_changed",
    label: "Task priority changed",
    value: "task_priority_changed",
    group: "Tasks",
  },
  {
    id: "task_workflow_changed",
    label: "Task workflow changed",
    value: "task_workflow_changed",
    group: "Tasks",
  },
  {
    id: "task_assignee_changed",
    label: "Task assignee changed",
    value: "task_assignee_changed",
    group: "Tasks",
  },
  {
    id: "dropdown_value_changed",
    label: "Dropdown value changed",
    value: "dropdown_value_changed",
    group: "Dropdowns",
  },
]

const CASE_EVENT_LABELS = new Map<string, string>(
  CASE_EVENT_SUGGESTIONS.map(({ value, label }) => [value, label])
)

export function getCaseEventLabel(value: CaseEventType): string {
  return (
    CASE_EVENT_LABELS.get(value) ??
    value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase())
  )
}
