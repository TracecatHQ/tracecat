"use client"

import {
  ArrowUpIcon,
  CircleUserRound,
  Plus,
  SignalHigh,
  Workflow,
  X,
} from "lucide-react"
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
} from "react"
import type {
  CaseTaskRead,
  WorkflowReadMinimal,
  WorkspaceMember,
} from "@/client"
import { PRIORITIES } from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { CaseTaskFieldMenu } from "@/components/cases/case-task-field-menu"
import { CaseTaskFieldPicker } from "@/components/cases/case-task-field-picker"
import {
  buildAssigneeItems,
  buildWorkflowItems,
  CASE_TASK_ROW_CLASS,
  type CaseTaskAssigneeDisplay,
  hasPrioritySignal,
  isCasePriority,
  isCaseTaskStatus,
  NO_WORKFLOW,
  PRIORITY_MENU_ITEMS,
  STATUS_MENU_ITEMS,
  TASK_ICON_TRIGGER_CLASS,
  TASK_PILL_CLASS,
  toAssigneeDisplay,
} from "@/components/cases/case-task-fields"
import {
  CASE_TASK_STATUSES,
  CaseTaskStatusIcon,
  priorityIconTone,
} from "@/components/cases/case-task-status"
import { Button } from "@/components/ui/button"
import UserAvatar from "@/components/user-avatar"
import {
  CASE_TASK_DESCRIPTION_MAX_LENGTH,
  useCaseTaskForm,
} from "@/hooks/use-case-task-form"
import { cn } from "@/lib/utils"

/**
 * One line of the description textarea, and its collapsed height. Matches the
 * `leading-6` the title and description share.
 */
const DESCRIPTION_LINE_HEIGHT_PX = 24

/** Props for {@link AddTaskRow}. */
export interface AddTaskRowProps {
  /** Swaps this row out for the new-task composer. */
  onClick: () => void
}

/**
 * Muted ghost row that swaps in place for {@link CaseTaskComposer}. Built to
 * the task row's geometry — same `h-11`, same inset, same 24px leading glyph
 * slot — so the plus sits exactly where a row's status icon does and the label
 * starts exactly where task titles do. Also the panel's only empty state:
 * there is no separate "No tasks" placeholder.
 */
export function AddTaskRow({ onClick }: AddTaskRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        CASE_TASK_ROW_CLASS,
        "flex h-11 w-full items-center gap-2 text-sm font-medium text-muted-foreground hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      )}
    >
      <span className="flex size-6 shrink-0 items-center justify-center">
        <Plus className="size-5" />
      </span>
      Add task
    </button>
  )
}

/** Props for {@link CaseTaskComposer}. */
export interface CaseTaskComposerProps {
  /** Task being edited, or `null` for the new-task composer. */
  task: CaseTaskRead | null
  caseId: string
  workspaceId: string
  workflows: readonly WorkflowReadMinimal[]
  members: readonly WorkspaceMember[]
  /** Leaves the composer: Cancel, Escape, and (when editing) a saved edit. */
  onClose: () => void
}

/**
 * The one composer behind both new tasks and edits — the same form either way,
 * only the absence of a `task` distinguishes them.
 *
 * Laid out on the row's grid: the status trigger takes the row's leading glyph
 * slot and the title input starts on the row titles' left edge, so opening a
 * composer never shifts the text column. A borderless title input, a raw
 * markdown textarea (Enter for a newline, Cmd/Ctrl+Enter to submit, a silent
 * 1000-character stop), the remaining field pills, and Cancel plus the
 * circular submit shared with the comment composer below.
 *
 * Escape cancels and stops propagating, or it would close the whole case panel
 * in slideover/embedded mode. It never closes on blur — clicking into a picker
 * popover blurs the form and must not discard the draft. After a successful
 * create it resets and stays open with focus back in the title, for rapid
 * entry; a successful edit save closes back to the read row.
 */
export function CaseTaskComposer({
  task,
  caseId,
  workspaceId,
  workflows,
  members,
  onClose,
}: CaseTaskComposerProps) {
  const titleRef = useRef<HTMLInputElement | null>(null)
  const descriptionRef = useRef<HTMLTextAreaElement | null>(null)

  const { form, submit, isPending } = useCaseTaskForm({
    caseId,
    workspaceId,
    task,
    onCreateSuccess: () => titleRef.current?.focus(),
    onUpdateSuccess: onClose,
  })

  // Focus via ref on mount, not `autoFocus`, so focus lands after the
  // add-row-to-composer (or row-to-composer) swap paints.
  useEffect(() => {
    titleRef.current?.focus()
  }, [])

  const { ref: registerTitleRef, ...titleField } = form.register("title")
  const { ref: registerDescriptionRef, ...descriptionField } =
    form.register("description")
  const title = form.watch("title")
  const description = form.watch("description")
  const status = form.watch("status")
  const priority = form.watch("priority")
  const assigneeId = form.watch("assignee_id")
  const workflowId = form.watch("workflow_id")

  // Grow the description to fit its content, with a one-line floor: the
  // composer stands in for a 36px row, so it opens at the row's height and
  // grows only as the user writes.
  useLayoutEffect(() => {
    const node = descriptionRef.current
    if (node) {
      node.style.height = "0px"
      node.style.height = `${Math.max(node.scrollHeight, DESCRIPTION_LINE_HEIGHT_PX)}px`
    }
  }, [description])

  const assignee = useMemo<CaseTaskAssigneeDisplay | null>(() => {
    if (!assigneeId) {
      return null
    }
    const member = members.find((item) => item.user_id === assigneeId)
    if (member) {
      return {
        id: member.user_id,
        email: member.email,
        firstName: member.first_name,
        lastName: member.last_name,
      }
    }
    // A departed member can still be the saved assignee; fall back to the
    // task's own record so the pill keeps showing who it is.
    if (task?.assignee && task.assignee.id === assigneeId) {
      return toAssigneeDisplay(task.assignee)
    }
    return null
  }, [assigneeId, members, task])

  const assigneeItems = useMemo(() => buildAssigneeItems(members), [members])
  const workflowItems = useMemo(
    () => buildWorkflowItems(workflows),
    [workflows]
  )

  const statusDefinition = CASE_TASK_STATUSES[status]
  const priorityDefinition = PRIORITIES[priority]
  const showPriority = hasPrioritySignal(priority)
  // Same substitution the read row makes: an unset priority shows the generic
  // signal glyph, not "unknown"'s question mark.
  const PriorityIcon = showPriority ? priorityDefinition.icon : SignalHigh
  const selectedWorkflow = workflowId
    ? workflows.find((workflow) => workflow.id === workflowId)
    : undefined

  function handleFormKeyDown(event: ReactKeyboardEvent<HTMLFormElement>) {
    if (event.key !== "Escape") {
      return
    }
    // Escape inside an open picker never reaches here — Radix portals its
    // content to document.body. This one is aimed at the case panel: without
    // stopPropagation it closes the whole panel in slideover/embedded mode
    // instead of just this composer.
    event.stopPropagation()
    event.preventDefault()
    onClose()
  }

  function handleDescriptionKeyDown(
    event: ReactKeyboardEvent<HTMLTextAreaElement>
  ) {
    // Enter falls through to the textarea's native newline; only the comment
    // composer's Cmd/Ctrl+Enter chord submits.
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      if (!isPending) {
        void submit()
      }
    }
  }

  return (
    <form
      onSubmit={submit}
      onKeyDown={handleFormKeyDown}
      // The row's box, verbatim: same inset, same radius. Editing a task must
      // not redraw the line it happens on — only the text turns into fields.
      className={CASE_TASK_ROW_CLASS}
    >
      <div className="flex h-11 items-center gap-2">
        {/* Status leads the composer exactly as it leads a row. */}
        <CaseTaskFieldMenu
          items={STATUS_MENU_ITEMS}
          value={status}
          onSelect={(value) => {
            if (isCaseTaskStatus(value)) {
              form.setValue("status", value)
            }
          }}
          ariaLabel="Change status"
          tooltip={statusDefinition.label}
        >
          <button
            type="button"
            aria-label={`Change status: ${statusDefinition.label}`}
            className={TASK_ICON_TRIGGER_CLASS}
          >
            <CaseTaskStatusIcon status={status} className="size-5" />
          </button>
        </CaseTaskFieldMenu>
        {/* Typographically identical to the read row's title span, so the
            text does not shift or restyle when the field takes over. */}
        <input
          {...titleField}
          ref={(node) => {
            registerTitleRef(node)
            titleRef.current = node
          }}
          placeholder="Task title"
          aria-label="Task title"
          className="min-w-0 flex-1 bg-transparent text-sm font-medium leading-6 text-foreground outline-none placeholder:text-muted-foreground/60"
        />
        {/* Shrinkable, not `shrink-0`: in a narrow panel — the chat artifact
            at its minimum width — a rigid cluster pushes cancel and submit out
            of an `overflow-hidden` container and the task can no longer be
            saved by pointer. The title has a 0 flex basis, so it takes none of
            the shrink and still collapses first; inside here only the workflow
            pill gives, down to its icon. */}
        <div className="flex min-w-0 items-center gap-1.5 pl-2">
          <CaseTaskFieldMenu
            items={PRIORITY_MENU_ITEMS}
            value={priority}
            onSelect={(value) => {
              if (isCasePriority(value)) {
                form.setValue("priority", value)
              }
            }}
            ariaLabel="Set priority"
            tooltip={showPriority ? priorityDefinition.label : "Set priority"}
          >
            <button
              type="button"
              aria-label={`Set priority: ${priorityDefinition.label}`}
              className={TASK_ICON_TRIGGER_CLASS}
            >
              <PriorityIcon
                className={cn(
                  "size-5",
                  showPriority
                    ? priorityIconTone(priority)
                    : "text-muted-foreground/50"
                )}
              />
            </button>
          </CaseTaskFieldMenu>
          <CaseTaskFieldPicker
            items={assigneeItems}
            value={assignee?.id ?? null}
            emptyValue={UNASSIGNED}
            onSelect={(value) => form.setValue("assignee_id", value)}
            placeholder="Assign to…"
            ariaLabel="Assign to"
            tooltip={assignee ? assignee.email : "Assign to"}
          >
            <button
              type="button"
              aria-label={
                assignee
                  ? `Assign to: currently ${assignee.email}`
                  : "Assign to"
              }
              className="flex size-6 shrink-0 items-center justify-center rounded-full transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {assignee ? (
                <UserAvatar
                  alt={assignee.firstName || assignee.email}
                  email={assignee.email}
                  firstName={assignee.firstName}
                  className="size-6"
                  fallbackClassName="text-[11px]"
                />
              ) : (
                <CircleUserRound className="size-5 text-muted-foreground/50" />
              )}
            </button>
          </CaseTaskFieldPicker>
          {/* The row runs the linked workflow; the composer picks it. Same
              pill in the same slot — right of the assignee — and it stays
              visible while unset because this is the surface where a workflow
              gets attached. */}
          <CaseTaskFieldPicker
            items={workflowItems}
            value={workflowId}
            emptyValue={NO_WORKFLOW}
            onSelect={(value) => form.setValue("workflow_id", value)}
            placeholder="Set workflow…"
            ariaLabel="Set workflow"
          >
            <button
              type="button"
              // `shrink min-w-0` overrides the pill's default `shrink-0`: this
              // is the one control in the cluster whose label can go, so it
              // absorbs the squeeze down to its icon and the buttons beside it
              // stay reachable.
              className={cn(
                TASK_PILL_CLASS,
                "min-w-0 max-w-36 shrink",
                selectedWorkflow
                  ? "text-muted-foreground hover:text-foreground"
                  : "text-muted-foreground"
              )}
            >
              <Workflow className="size-3.5 shrink-0" />
              <span className="min-w-0 truncate">
                {selectedWorkflow ? selectedWorkflow.title : "Workflow"}
              </span>
            </button>
          </CaseTaskFieldPicker>
          {/* Where the read row keeps its ⋯: two glyphs, so the composer ends
              on the same right edge instead of growing an action bar. */}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Cancel"
            className="ml-1 size-6 shrink-0 rounded text-muted-foreground hover:text-foreground"
            onClick={onClose}
            disabled={isPending}
          >
            <X className="size-4" />
          </Button>
          <Button
            type="submit"
            variant="outline"
            size="icon"
            className="size-6 shrink-0 rounded-full border-border/70"
            disabled={isPending || !title.trim()}
            aria-label={task ? "Save task" : "Create task"}
          >
            <ArrowUpIcon className="size-3.5" />
          </Button>
        </div>
      </div>
      {/* Raw markdown, exactly like the comment composer below the panel —
          never a rich-text editor. maxLength is the whole length story: a
          silent stop at the database limit, with no validation copy. pl-8
          lands it on the title's left edge, where the read row renders an
          expanded description. */}
      <textarea
        {...descriptionField}
        ref={(node) => {
          registerDescriptionRef(node)
          descriptionRef.current = node
        }}
        onKeyDown={handleDescriptionKeyDown}
        placeholder="Add description…"
        aria-label="Task description"
        maxLength={CASE_TASK_DESCRIPTION_MAX_LENGTH}
        rows={1}
        className="mb-1.5 w-full resize-none overflow-hidden bg-transparent pl-8 pr-1 text-sm leading-6 outline-none placeholder:text-muted-foreground/60"
      />
    </form>
  )
}
