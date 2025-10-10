import type { LucideIcon } from "lucide-react"
import {
  CircleCheck,
  FilePlus2,
  Flag,
  Flame,
  GitCompare,
  PenSquare,
  RotateCcw,
  UserRound,
} from "lucide-react"

import type { CaseDurationAnchorSelection, CaseEventType } from "@/client"

import { PRIORITIES, SEVERITIES, STATUSES } from "./case-categories"

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
    value: "assignee_changed",
    label: "Assignee changed",
    icon: UserRound,
  },
]

export const CASE_EVENT_VALUES = CASE_EVENT_OPTIONS.map(
  (option) => option.value
) as [CaseEventType, ...CaseEventType[]]

export const CASE_DURATION_SELECTION_OPTIONS: Array<{
  value: CaseDurationAnchorSelection
  label: string
}> = [
  { value: "first", label: "First seen" },
  { value: "last", label: "Last seen" },
]

type CaseEventFilterType = Extract<
  CaseEventType,
  "priority_changed" | "severity_changed" | "status_changed"
>

export interface CaseEventFilterOption {
  value: string
  label: string
}

export const CASE_EVENT_FILTER_OPTIONS = {
  priority_changed: {
    label: "Priority",
    options: Object.values(PRIORITIES).map(({ value, label }) => ({
      value,
      label,
    })),
  },
  severity_changed: {
    label: "Severity",
    options: Object.values(SEVERITIES).map(({ value, label }) => ({
      value,
      label,
    })),
  },
  status_changed: {
    label: "Status",
    options: Object.values(STATUSES).map(({ value, label }) => ({
      value,
      label,
    })),
  },
} as const satisfies Record<
  CaseEventFilterType,
  {
    label: string
    options: CaseEventFilterOption[]
  }
>

export function isCaseEventFilterType(
  value: CaseEventType
): value is CaseEventFilterType {
  return value in CASE_EVENT_FILTER_OPTIONS
}

export function getCaseEventOption(value: CaseEventType): CaseEventOption {
  return (
    CASE_EVENT_OPTIONS.find((option) => option.value === value) || {
      value,
      label: value
        .replace(/_/g, " ")
        .replace(/\b\w/g, (char) => char.toUpperCase()),
      icon: FilePlus2,
    }
  )
}
