"use client"

import { CircleSlash } from "lucide-react"
import type {
  CasePriority,
  CaseTaskStatus,
  UserRead,
  WorkflowReadMinimal,
  WorkspaceMember,
} from "@/client"
import { PRIORITIES } from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import type { CaseTaskFieldMenuItem } from "@/components/cases/case-task-field-menu"
import type { CaseTaskFieldPickerItem } from "@/components/cases/case-task-field-picker"
import {
  CASE_TASK_STATUS_VALUES,
  CASE_TASK_STATUSES,
  priorityIconTone,
} from "@/components/cases/case-task-status"
import UserAvatar from "@/components/user-avatar"

/**
 * The box a task line lives in, read and edit alike. Borderless and 44px tall:
 * the list reads as a stack of lines, not a stack of cards, so nothing but the
 * hover tint separates one task from the next. `px-3` inside the panel
 * container's `p-2` lands row text 20px from the container's edge, on the
 * comment cards' `px-5` inset.
 */
export const CASE_TASK_ROW_CLASS = "rounded-md px-3 transition-colors"

/**
 * 24px hit target around a 20px glyph — the row's status and priority
 * triggers, and the composer's status trigger. 24px is the row's one control
 * height: the avatar and the workflow pill match it, so the whole metadata
 * cluster shares a baseline. Bare by design: the list is a flat stack of rows,
 * so a bordered control on every line would read as chrome. The hover tint is
 * the only affordance.
 */
export const TASK_ICON_TRIGGER_CLASS =
  "flex size-6 shrink-0 items-center justify-center rounded transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"

/**
 * Bordered metadata pill: the row's workflow run trigger and the composer's
 * field triggers. `h-6` is the shared 24px control height, clearing the 44px
 * row with 10px above and below.
 */
export const TASK_PILL_CLASS =
  "flex h-6 shrink-0 items-center gap-1 rounded-md border border-border px-2 text-xs transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"

/**
 * Rest-quiet treatment for a row control: invisible until the row is hovered,
 * the control is keyboard-focused, or its menu is open. Opacity, not `hidden`,
 * so the row's right cluster never reflows. Mirrors the trigger-field pattern
 * in `workflow-trigger-form.tsx`.
 */
export const TASK_HOVER_REVEAL_CLASS =
  "pointer-events-none opacity-0 transition-opacity duration-150 group-hover/task:pointer-events-auto group-hover/task:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100 data-[state=open]:pointer-events-auto data-[state=open]:opacity-100"

/**
 * Palette and submenu value for "no workflow". Never leaves the task UI: both
 * the row and the composer map it to `null` before it reaches the API, so it
 * does not have to match anyone else's sentinel.
 */
export const NO_WORKFLOW = "__no_workflow__"

/** Narrows a picker value to the generated `CaseTaskStatus` union. */
export function isCaseTaskStatus(value: string): value is CaseTaskStatus {
  return (CASE_TASK_STATUS_VALUES as readonly string[]).includes(value)
}

/** Narrows a picker value to the generated `CasePriority` union. */
export function isCasePriority(value: string): value is CasePriority {
  return value in PRIORITIES
}

/** Whether a priority carries signal worth rendering at rest. */
export function hasPrioritySignal(priority: CasePriority): boolean {
  return priority !== "unknown" && priority !== "other"
}

/** Assignee fields the task UI needs, normalized across read and edit mode. */
export interface CaseTaskAssigneeDisplay {
  id: string
  email: string
  firstName?: string | null
  lastName?: string | null
}

/** Normalizes a task's assignee (a `UserRead`) for display. */
export function toAssigneeDisplay(user: UserRead): CaseTaskAssigneeDisplay {
  return {
    id: user.id,
    email: user.email,
    firstName: user.first_name,
    lastName: user.last_name,
  }
}

/** Short display name for an assignee: full name, else email. */
export function assigneeDisplayName(assignee: CaseTaskAssigneeDisplay): string {
  return (
    [assignee.firstName, assignee.lastName].filter(Boolean).join(" ") ||
    assignee.email
  )
}

/** Dropdown rows for the status menu; static, so built once. */
export const STATUS_MENU_ITEMS: CaseTaskFieldMenuItem[] =
  CASE_TASK_STATUS_VALUES.map((value) => {
    const definition = CASE_TASK_STATUSES[value]
    return {
      value,
      label: definition.label,
      icon: definition.icon,
      iconClassName: definition.iconClassName,
    }
  })

/** Dropdown rows for the priority menu; static, so built once. */
export const PRIORITY_MENU_ITEMS: CaseTaskFieldMenuItem[] = Object.values(
  PRIORITIES
).map((priority) => ({
  value: priority.value,
  label: priority.label,
  icon: priority.icon,
  iconClassName: priorityIconTone(priority.value),
}))

/**
 * Members sorted by email — the field the palette and submenu render, and
 * therefore the order a user reads as alphabetical.
 */
export function sortMembersByEmail(
  members: readonly WorkspaceMember[]
): WorkspaceMember[] {
  return [...members].sort((a, b) => a.email.localeCompare(b.email))
}

/**
 * Palette rows for the assignee picker: the "No assignee" row plus every
 * member, email-sorted. Shared by the row's avatar trigger and the composer.
 */
export function buildAssigneeItems(
  members: readonly WorkspaceMember[]
): CaseTaskFieldPickerItem[] {
  return [
    {
      value: UNASSIGNED,
      label: "No assignee",
      icon: CircleSlash,
      iconClassName: "text-muted-foreground/70",
    },
    ...sortMembersByEmail(members).map((member) => ({
      value: member.user_id,
      label: member.email,
      leading: (
        <UserAvatar
          alt={member.first_name || member.email}
          email={member.email}
          firstName={member.first_name}
          className="size-4"
          fallbackClassName="text-[10px]"
        />
      ),
      searchValue: [member.first_name, member.last_name]
        .filter(Boolean)
        .join(" "),
    })),
  ]
}

/** Palette rows for the workflow picker: "No workflow" plus every workflow. */
export function buildWorkflowItems(
  workflows: readonly WorkflowReadMinimal[]
): CaseTaskFieldPickerItem[] {
  return [
    {
      value: NO_WORKFLOW,
      label: "No workflow",
      icon: CircleSlash,
      iconClassName: "text-muted-foreground/70",
    },
    ...workflows.map((workflow) => ({
      value: workflow.id,
      label: workflow.alias
        ? `${workflow.title} (${workflow.alias})`
        : workflow.title,
      searchValue: workflow.alias ?? undefined,
    })),
  ]
}
