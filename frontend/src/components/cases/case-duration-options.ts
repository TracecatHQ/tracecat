import type { LucideIcon } from "lucide-react"
import {
  CircleCheck,
  Eye,
  FilePlus2,
  Flag,
  Flame,
  FormInput,
  GitCompare,
  List,
  MessageSquarePlus,
  MessageSquareX,
  PenSquare,
  RotateCcw,
  Tag,
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
    value: "case_viewed",
    label: "Case viewed",
    icon: Eye,
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
  {
    value: "tag_added",
    label: "Tag added",
    icon: Tag,
  },
  {
    value: "tag_removed",
    label: "Tag removed",
    icon: Tag,
  },
  {
    value: "fields_changed",
    label: "Custom field changed",
    icon: FormInput,
  },
  {
    value: "dropdown_value_changed",
    label: "Dropdown value changed",
    icon: List,
  },
  {
    value: "comment_created",
    label: "Comment added",
    icon: MessageSquarePlus,
  },
  {
    value: "comment_deleted",
    label: "Comment deleted",
    icon: MessageSquareX,
  },
  {
    value: "comment_reply_created",
    label: "Comment reply added",
    icon: MessageSquarePlus,
  },
  {
    value: "comment_reply_deleted",
    label: "Comment reply deleted",
    icon: MessageSquareX,
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

export interface CaseEventFilterSelectOption extends CaseEventFilterOption {
  icon?: LucideIcon
  iconClassName?: string
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

const CATEGORY_OPTIONS = {
  priority_changed: PRIORITIES,
  severity_changed: SEVERITIES,
  status_changed: STATUSES,
} as const

const getOptionIconClass = (color?: string) =>
  color?.split(" ").find((token) => token.startsWith("text-")) ||
  "text-muted-foreground"

export function buildCaseEventFilterOptions(
  eventType: CaseEventFilterType
): CaseEventFilterSelectOption[] {
  const categoryMap = CATEGORY_OPTIONS[eventType] as Record<
    string,
    { icon: LucideIcon; color?: string }
  >

  return CASE_EVENT_FILTER_OPTIONS[eventType].options.map((option) => {
    const category = categoryMap[option.value]
    return {
      value: option.value,
      label: option.label,
      icon: category?.icon,
      iconClassName: getOptionIconClass(category?.color),
    }
  })
}

const CASE_TAG_EVENT_TYPES = [
  "tag_added",
  "tag_removed",
] as const satisfies readonly CaseEventType[]

export type CaseTagEventType = (typeof CASE_TAG_EVENT_TYPES)[number]

export function isCaseTagEventType(
  value: CaseEventType
): value is CaseTagEventType {
  return (CASE_TAG_EVENT_TYPES as readonly CaseEventType[]).includes(value)
}

export function isCaseFieldEventType(value: CaseEventType): boolean {
  return value === "fields_changed"
}

export function isCaseDropdownEventType(value: CaseEventType): boolean {
  return value === "dropdown_value_changed"
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
