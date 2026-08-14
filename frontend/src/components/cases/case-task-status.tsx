"use client"

import {
  Circle,
  CircleCheck,
  CircleDashed,
  CircleDot,
  type LucideIcon,
} from "lucide-react"
import type { CasePriority, CaseTaskRead, CaseTaskStatus } from "@/client"
import { PRIORITIES } from "@/components/cases/case-categories"
import type { CaseTaskProgress } from "@/components/cases/case-panel-switcher"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn, linearStyles } from "@/lib/utils"

/**
 * Case task statuses in display order. `satisfies` pins the tuple to the
 * generated `CaseTaskStatus` union, so a backend contract change becomes a
 * compile error here instead of a silently drifting hand copy.
 */
export const CASE_TASK_STATUS_VALUES = [
  "todo",
  "in_progress",
  "completed",
  "blocked",
] as const satisfies readonly CaseTaskStatus[]

/**
 * Statuses that count as done for task progress. `blocked` deliberately does
 * not count — it is outstanding work that needs attention, and counting it
 * would understate what's left.
 */
const DONE_CASE_TASK_STATUSES = [
  "completed",
] as const satisfies readonly CaseTaskStatus[]

/** Static display definition for one case task status. */
export interface CaseTaskStatusDefinition {
  value: CaseTaskStatus
  label: string
  icon: LucideIcon
  /** Text color utilities for the status icon. */
  iconClassName: string
}

/**
 * Display definitions per task status. A `Record` over the generated union so
 * both a missing and an extra key fail to compile.
 */
export const CASE_TASK_STATUSES: Record<
  CaseTaskStatus,
  CaseTaskStatusDefinition
> = {
  todo: {
    value: "todo",
    label: "To do",
    icon: Circle,
    iconClassName: "text-muted-foreground",
  },
  in_progress: {
    value: "in_progress",
    label: "In progress",
    icon: CircleDot,
    iconClassName: "text-primary",
  },
  completed: {
    value: "completed",
    label: "Completed",
    icon: CircleCheck,
    iconClassName: "text-success",
  },
  blocked: {
    value: "blocked",
    label: "Blocked",
    icon: CircleDashed,
    // An explicit red pair, not text-destructive: --destructive is a
    // *background* token that inverts darker in dark mode (60.2% -> 30.6%),
    // so as text it goes muddy on the near-black dark background.
    iconClassName: "text-red-600 dark:text-red-400",
  },
}

/**
 * Text color utilities for a priority icon: an urgency ramp, red at the top.
 * Explicit light/dark pairs, never `text-destructive` — `--destructive` is a
 * background token that inverts darker in dark mode, so as text it goes muddy
 * on the near-black dark background.
 */
export function priorityIconTone(priority: CasePriority): string {
  switch (priority) {
    case "critical":
    case "high":
      return "text-red-600 dark:text-red-400"
    case "medium":
      return "text-amber-600 dark:text-amber-400"
    default:
      return "text-muted-foreground"
  }
}

/** Returns whether a task status counts as done for progress purposes. */
export function isCaseTaskDone(status: CaseTaskStatus): boolean {
  return (DONE_CASE_TASK_STATUSES as readonly CaseTaskStatus[]).includes(status)
}

/**
 * Counts done/total task progress for the switcher ring and the Tasks button.
 * The single source of truth — the switcher calls this rather than recounting
 * inline, so the ring and the panel cannot disagree.
 */
export function getCaseTaskProgress(
  tasks: readonly CaseTaskRead[] | undefined
): CaseTaskProgress {
  const list = tasks ?? []
  return {
    done: list.filter((task) => isCaseTaskDone(task.status)).length,
    total: list.length,
  }
}

/** Props for {@link CaseTaskStatusIcon}. */
export interface CaseTaskStatusIconProps {
  status: CaseTaskStatus
  className?: string
}

/** The status icon for a case task, colored per status. */
export function CaseTaskStatusIcon({
  status,
  className,
}: CaseTaskStatusIconProps) {
  const definition = CASE_TASK_STATUSES[status]
  const Icon = definition.icon
  return <Icon className={cn("size-4", definition.iconClassName, className)} />
}

/** Props for {@link CaseTaskStatusSelect}. */
export interface CaseTaskStatusSelectProps {
  status: CaseTaskStatus
  onValueChange: (status: CaseTaskStatus) => void
  triggerClassName?: string
}

/**
 * Compact status picker for case tasks. Distinct from `StatusSelect` in
 * `case-panel-selectors.tsx`, which selects a `CaseStatus` — a different enum.
 */
export function CaseTaskStatusSelect({
  status,
  onValueChange,
  triggerClassName,
}: CaseTaskStatusSelectProps) {
  const current = CASE_TASK_STATUSES[status]
  return (
    <Select value={status} onValueChange={onValueChange}>
      <SelectTrigger
        aria-label="Status"
        className={cn(
          linearStyles.trigger.base,
          linearStyles.trigger.hover,
          triggerClassName
        )}
      >
        <SelectValue>
          <span className="flex items-center gap-1.5">
            <CaseTaskStatusIcon status={status} className="size-3.5" />
            <span>{current.label}</span>
          </span>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {CASE_TASK_STATUS_VALUES.map((value) => (
          <SelectItem key={value} value={value}>
            <span className="flex items-center gap-1.5">
              <CaseTaskStatusIcon status={value} className="size-3.5" />
              <span className="text-xs">{CASE_TASK_STATUSES[value].label}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** Props for {@link CaseTaskPriorityChip}. */
export interface CaseTaskPriorityChipProps {
  priority: CasePriority
  className?: string
}

/**
 * Flat icon + label chip for a task's priority — no badge background. Renders
 * nothing for both `unknown` and `other`: neither carries signal, and `other`
 * used to render an empty-colored badge.
 */
export function CaseTaskPriorityChip({
  priority,
  className,
}: CaseTaskPriorityChipProps) {
  if (priority === "unknown" || priority === "other") {
    return null
  }
  const definition = PRIORITIES[priority]
  const Icon = definition.icon
  return (
    <span
      className={cn(
        "flex items-center gap-1 text-xs text-muted-foreground",
        className
      )}
    >
      <Icon className="size-3.5" />
      {definition.label}
    </span>
  )
}
