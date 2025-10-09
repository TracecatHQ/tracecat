import type { LucideIcon } from "lucide-react"
import {
  Braces,
  CircleCheck,
  Database,
  FilePlus2,
  Flag,
  Flame,
  GitCompare,
  Paperclip,
  PenSquare,
  RotateCcw,
  Trash2,
  UserRound,
} from "lucide-react"

import type {
  CaseDurationAnchorSelection,
  CaseEventType,
} from "@/types/case-durations"

export interface CaseEventOption {
  value: CaseEventType
  label: string
  icon: LucideIcon
  description?: string
}

export const CASE_EVENT_OPTIONS: CaseEventOption[] = [
  {
    value: "case_created",
    label: "Case created",
    icon: FilePlus2,
  },
  {
    value: "case_updated",
    label: "Case updated",
    icon: PenSquare,
  },
  {
    value: "case_closed",
    label: "Case closed",
    icon: CircleCheck,
  },
  {
    value: "case_reopened",
    label: "Case reopened",
    icon: RotateCcw,
  },
  {
    value: "priority_changed",
    label: "Priority changed",
    icon: Flag,
  },
  {
    value: "severity_changed",
    label: "Severity changed",
    icon: Flame,
  },
  {
    value: "status_changed",
    label: "Status changed",
    icon: GitCompare,
  },
  {
    value: "fields_changed",
    label: "Fields changed",
    icon: Braces,
  },
  {
    value: "assignee_changed",
    label: "Assignee changed",
    icon: UserRound,
  },
  {
    value: "attachment_created",
    label: "Attachment added",
    icon: Paperclip,
  },
  {
    value: "attachment_deleted",
    label: "Attachment removed",
    icon: Trash2,
  },
  {
    value: "payload_changed",
    label: "Payload changed",
    icon: Database,
  },
]

export const CASE_EVENT_VALUES = CASE_EVENT_OPTIONS.map(
  (option) => option.value
) as [CaseEventType, ...CaseEventType[]]

export const CASE_DURATION_SELECTION_OPTIONS: Array<{
  value: CaseDurationAnchorSelection
  label: string
}> = [
  { value: "first", label: "First matching event" },
  { value: "last", label: "Last matching event" },
]

export function getCaseEventOption(value: CaseEventType): CaseEventOption {
  return (
    CASE_EVENT_OPTIONS.find((option) => option.value === value) || {
      value,
      label: value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()),
      icon: Database,
    }
  )
}
