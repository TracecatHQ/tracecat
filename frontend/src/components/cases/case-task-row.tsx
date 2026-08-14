"use client"

import {
  ChevronRight,
  CircleDot,
  CircleSlash,
  CircleUserRound,
  Pencil,
  Play,
  SignalHigh,
  Trash2,
  User,
  Workflow,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type {
  CasePriority,
  CaseRead,
  CaseTaskRead,
  CaseTaskStatus,
  WorkflowReadMinimal,
  WorkspaceMember,
} from "@/client"
import { PRIORITIES } from "@/components/cases/case-categories"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { CaseTaskComposer } from "@/components/cases/case-task-composer"
import { CaseTaskFieldMenu } from "@/components/cases/case-task-field-menu"
import { CaseTaskFieldPicker } from "@/components/cases/case-task-field-picker"
import {
  buildAssigneeItems,
  CASE_TASK_ROW_CLASS,
  type CaseTaskAssigneeDisplay,
  hasPrioritySignal,
  isCasePriority,
  isCaseTaskStatus,
  NO_WORKFLOW,
  PRIORITY_MENU_ITEMS,
  STATUS_MENU_ITEMS,
  sortMembersByEmail,
  TASK_HOVER_REVEAL_CLASS,
  TASK_ICON_TRIGGER_CLASS,
  TASK_PILL_CLASS,
  toAssigneeDisplay,
} from "@/components/cases/case-task-fields"
import {
  CASE_TASK_STATUS_VALUES,
  CASE_TASK_STATUSES,
  CaseTaskStatusIcon,
  priorityIconTone,
} from "@/components/cases/case-task-status"
import { WorkflowTriggerDialog } from "@/components/cases/workflow-trigger-dialog"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuRadioGroup,
  ContextMenuRadioItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import UserAvatar from "@/components/user-avatar"
import { useUpdateCaseTask } from "@/lib/hooks"
import { cn } from "@/lib/utils"

/** Props for {@link CaseTaskRow}. */
export interface CaseTaskRowProps {
  task: CaseTaskRead
  /** Whether this row is the panel's one row in edit mode. */
  editing: boolean
  /** Workflow linked to the task, resolved by the panel. */
  workflow: WorkflowReadMinimal | null
  /** All workspace workflows, for the composer picker and submenu. */
  workflows: readonly WorkflowReadMinimal[]
  /** Workspace members, for the assignee palette and submenu. */
  members: readonly WorkspaceMember[]
  caseData: CaseRead
  caseId: string
  workspaceId: string
  /** Asks the panel to make this the row in edit mode. */
  onRequestEdit: (task: CaseTaskRead) => void
  /** Tells the panel this row left edit mode. */
  onCloseEdit: () => void
  /** Opens the delete confirm hosted by the panel. */
  onDelete: (task: CaseTaskRead) => void
}

/**
 * One task, as a single dense line: the status glyph, the title, and a
 * right-aligned metadata cluster of priority, the workflow run pill, and the
 * assignee avatar. Every glyph in the row is its own live picker, so a status
 * or assignee change costs one click and never opens the composer. There is no
 * `⋯`: right-click is the row's menu, carrying the four field submenus plus
 * Edit and Delete. Edit mode swaps the line for {@link CaseTaskComposer}.
 *
 * Every patch here is a single-field `useUpdateCaseTask` call. Never spread a
 * wider object into it: `default_trigger_values` must never ride along, or it
 * erases trigger defaults written by `WorkflowTriggerDialog`.
 */
export function CaseTaskRow({
  task,
  editing,
  workflow,
  workflows,
  members,
  caseData,
  caseId,
  workspaceId,
  onRequestEdit,
  onCloseEdit,
  onDelete,
}: CaseTaskRowProps) {
  if (editing) {
    return (
      <CaseTaskComposer
        task={task}
        caseId={caseId}
        workspaceId={workspaceId}
        workflows={workflows}
        members={members}
        onClose={onCloseEdit}
      />
    )
  }
  return (
    <CaseTaskReadRow
      task={task}
      workflow={workflow}
      workflows={workflows}
      members={members}
      caseData={caseData}
      caseId={caseId}
      workspaceId={workspaceId}
      onRequestEdit={onRequestEdit}
      onDelete={onDelete}
    />
  )
}

/**
 * In-flight single-field patches, merged over the server's task for
 * everything the read face shows and guards. `null` means explicitly
 * cleared; an absent key means "no patch in flight, read the server".
 */
interface PendingTaskPatch {
  status?: CaseTaskStatus
  priority?: CasePriority
  assignee_id?: string | null
  workflow_id?: string | null
}

/** Props for the read face of {@link CaseTaskRow}. */
interface CaseTaskReadRowProps {
  task: CaseTaskRead
  workflow: WorkflowReadMinimal | null
  workflows: readonly WorkflowReadMinimal[]
  members: readonly WorkspaceMember[]
  caseData: CaseRead
  caseId: string
  workspaceId: string
  onRequestEdit: (task: CaseTaskRead) => void
  onDelete: (task: CaseTaskRead) => void
}

/** The row's read face. */
function CaseTaskReadRow({
  task,
  workflow,
  workflows,
  members,
  caseData,
  caseId,
  workspaceId,
  onRequestEdit,
  onDelete,
}: CaseTaskReadRowProps) {
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const [runDialogOpen, setRunDialogOpen] = useState(false)
  const [descriptionExpanded, setDescriptionExpanded] = useState(false)

  const { updateTask } = useUpdateCaseTask({
    caseId,
    workspaceId,
    taskId: task.id,
  })

  const sortedMembers = useMemo(() => sortMembersByEmail(members), [members])
  const assigneeItems = useMemo(() => buildAssigneeItems(members), [members])

  // Patches applied before the mutation lands. `updateTask` only invalidates
  // on success, so between a patch and the case-tasks refetch the `task` prop
  // lags the server — reading it live would pin the menus to stale values and
  // silently discard a quick re-selection of the original one. Each field is
  // reverted on mutation failure and pruned once the server reflects it, so
  // the overlay never masks a later change made elsewhere.
  const [pendingPatch, setPendingPatch] = useState<PendingTaskPatch>({})

  useEffect(() => {
    setPendingPatch((prev) => {
      const next = { ...prev }
      if (next.status === task.status) {
        delete next.status
      }
      if (next.priority === task.priority) {
        delete next.priority
      }
      if (next.assignee_id === (task.assignee?.id ?? null)) {
        delete next.assignee_id
      }
      if (next.workflow_id === (task.workflow_id ?? null)) {
        delete next.workflow_id
      }
      return Object.keys(next).length === Object.keys(prev).length ? prev : next
    })
  }, [task.status, task.priority, task.assignee?.id, task.workflow_id])

  const status = pendingPatch.status ?? task.status
  const priority = pendingPatch.priority ?? task.priority
  const assigneeId =
    pendingPatch.assignee_id !== undefined
      ? pendingPatch.assignee_id
      : (task.assignee?.id ?? null)
  const workflowId =
    pendingPatch.workflow_id !== undefined
      ? pendingPatch.workflow_id
      : (task.workflow_id ?? null)

  // A pending assignee arrives as a bare id, so its display resolves through
  // the member list rather than the task's hydrated assignee.
  let assignee: CaseTaskAssigneeDisplay | null = null
  if (pendingPatch.assignee_id === undefined) {
    assignee = task.assignee ? toAssigneeDisplay(task.assignee) : null
  } else if (pendingPatch.assignee_id !== null) {
    const member = members.find(
      (item) => item.user_id === pendingPatch.assignee_id
    )
    assignee = member
      ? {
          id: member.user_id,
          email: member.email,
          firstName: member.first_name,
          lastName: member.last_name,
        }
      : null
  }
  // The run pill follows the overlay too, resolved from the workspace list
  // because the panel's `workflow` prop is derived from the stale task.
  const resolvedWorkflow =
    pendingPatch.workflow_id === undefined
      ? workflow
      : (workflows.find((item) => item.id === pendingPatch.workflow_id) ?? null)

  const statusDefinition = CASE_TASK_STATUSES[status]
  const priorityDefinition = PRIORITIES[priority]
  const showPriority = hasPrioritySignal(priority)
  // Unset priority still needs a way in, so the slot keeps a generic signal
  // glyph that only surfaces on hover.
  const PriorityIcon = showPriority ? priorityDefinition.icon : SignalHigh

  // --- Single-field patches -----------------------------------------------
  function patchTask(patch: PendingTaskPatch) {
    setPendingPatch((prev) => ({ ...prev, ...patch }))
    updateTask(patch, {
      onError: () => {
        // Fall back to server truth for this field alone (the hook already
        // toasts) — unless a newer patch to it is the one on display.
        setPendingPatch((prev) => {
          const next = { ...prev }
          for (const key of Object.keys(patch) as (keyof PendingTaskPatch)[]) {
            if (next[key] === patch[key]) {
              delete next[key]
            }
          }
          return next
        })
      },
    })
  }

  function patchStatus(value: CaseTaskStatus) {
    if (value !== status) {
      patchTask({ status: value })
    }
  }

  function patchPriority(value: CasePriority) {
    if (value !== priority) {
      patchTask({ priority: value })
    }
  }

  function patchAssignee(value: string | null) {
    if (value !== assigneeId) {
      patchTask({ assignee_id: value })
    }
  }

  function patchWorkflow(value: string | null) {
    if (value !== workflowId) {
      patchTask({ workflow_id: value })
    }
  }

  return (
    <>
      <ContextMenu onOpenChange={setContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            className={cn(
              CASE_TASK_ROW_CLASS,
              "group/task",
              contextMenuOpen ? "bg-muted/50" : "hover:bg-muted/50"
            )}
          >
            <div className="flex h-11 items-center gap-2">
              <CaseTaskFieldMenu
                items={STATUS_MENU_ITEMS}
                value={status}
                onSelect={(value) => {
                  if (isCaseTaskStatus(value)) {
                    patchStatus(value)
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
              {/* Inert on purpose — editing goes through ⋯ → Edit. The title
                  truncates rather than wrapping, so the row stays one line at
                  any width and the metadata cluster never gets pushed off. */}
              <div className="flex min-w-0 flex-1 items-center gap-1">
                <span className="min-w-0 truncate text-sm font-medium leading-6 text-foreground">
                  {task.title}
                </span>
                {/* Disclosure caret, rendered only when a description exists,
                    sitting after the title so the title's position is
                    identical either way. */}
                {task.description ? (
                  <button
                    type="button"
                    aria-expanded={descriptionExpanded}
                    aria-label={
                      descriptionExpanded
                        ? "Hide description"
                        : "Show description"
                    }
                    onClick={() => setDescriptionExpanded((prev) => !prev)}
                    className={cn(
                      TASK_ICON_TRIGGER_CLASS,
                      "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <ChevronRight
                      className={cn(
                        "size-3.5 transition-transform duration-150",
                        descriptionExpanded && "rotate-90"
                      )}
                    />
                  </button>
                ) : null}
              </div>
              {/* Metadata cluster, right-aligned: priority, the assignee, then
                  the workflow run pill. Every control in it is 24px tall, so
                  the glyphs share one baseline with the pill. The run pill sits
                  last because it is the only action here — the two glyphs left
                  of it are fields, and an action reads better at the edge. */}
              <div className="flex shrink-0 items-center gap-1.5 pl-2">
                <CaseTaskFieldMenu
                  items={PRIORITY_MENU_ITEMS}
                  value={priority}
                  onSelect={(value) => {
                    if (isCasePriority(value)) {
                      patchPriority(value)
                    }
                  }}
                  ariaLabel="Set priority"
                  tooltip={
                    showPriority ? priorityDefinition.label : "Set priority"
                  }
                >
                  <button
                    type="button"
                    aria-label={`Set priority: ${priorityDefinition.label}`}
                    className={cn(
                      TASK_ICON_TRIGGER_CLASS,
                      !showPriority && TASK_HOVER_REVEAL_CLASS
                    )}
                  >
                    <PriorityIcon
                      className={cn(
                        "size-5",
                        showPriority
                          ? priorityIconTone(priority)
                          : "text-muted-foreground"
                      )}
                    />
                  </button>
                </CaseTaskFieldMenu>
                <CaseTaskFieldPicker
                  items={assigneeItems}
                  value={assigneeId}
                  emptyValue={UNASSIGNED}
                  onSelect={patchAssignee}
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
                {/* The workflow pill is the run button, and it disappears
                    entirely when no workflow is set. Picking a workflow lives
                    in the composer and the context menu. */}
                {resolvedWorkflow && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`Run workflow: ${resolvedWorkflow.title}`}
                        onClick={() => setRunDialogOpen(true)}
                        className={cn(
                          TASK_PILL_CLASS,
                          "max-w-36 text-muted-foreground hover:text-foreground"
                        )}
                      >
                        <Play className="size-3.5 shrink-0" />
                        <span className="truncate">
                          {resolvedWorkflow.title}
                        </span>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs">
                      Run workflow
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>
            </div>
            {/* Markdown, rendered exactly like a comment — but only once the
                caret opens it. pl-8 lands it on the title's left edge: the
                24px status trigger plus the row's 8px gap. */}
            {descriptionExpanded && task.description ? (
              <div className="min-w-0 pb-2 pl-8 pr-1 text-sm leading-6">
                <CaseCommentViewer
                  content={task.description}
                  workspaceId={workspaceId}
                />
              </div>
            ) : null}
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          {/* The row carries no `⋯`: right-click is the whole menu. Edit leads
              it — it is the one item that opens something rather than setting
              a field — then the four field submenus, then Delete. "Edit task"
              rather than "Edit": the menu also edits fields, so the bare verb
              did not say what it opened. */}
          <ContextMenuItem
            className="text-xs"
            onClick={() => onRequestEdit(task)}
          >
            <Pencil className="mr-2 size-3.5" />
            Edit task
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuSub>
            <ContextMenuSubTrigger className="text-xs">
              <CircleDot className="mr-2 size-3.5" />
              Change status
            </ContextMenuSubTrigger>
            <ContextMenuSubContent className="w-40">
              <ContextMenuRadioGroup value={status}>
                {CASE_TASK_STATUS_VALUES.map((value) => {
                  const definition = CASE_TASK_STATUSES[value]
                  const Icon = definition.icon
                  return (
                    <ContextMenuRadioItem
                      key={value}
                      value={value}
                      className="text-xs"
                      onClick={() => patchStatus(value)}
                    >
                      <Icon
                        className={cn(
                          "mr-2 size-3.5",
                          definition.iconClassName
                        )}
                      />
                      {definition.label}
                    </ContextMenuRadioItem>
                  )
                })}
              </ContextMenuRadioGroup>
            </ContextMenuSubContent>
          </ContextMenuSub>
          <ContextMenuSub>
            <ContextMenuSubTrigger className="text-xs">
              <User className="mr-2 size-3.5" />
              Assign to
            </ContextMenuSubTrigger>
            <ContextMenuSubContent className="w-56">
              <ContextMenuRadioGroup value={assigneeId ?? UNASSIGNED}>
                <ContextMenuRadioItem
                  value={UNASSIGNED}
                  className="text-xs"
                  onClick={() => patchAssignee(null)}
                >
                  <CircleSlash className="mr-2 size-3 text-muted-foreground/50" />
                  No assignee
                </ContextMenuRadioItem>
                {sortedMembers.map((member) => (
                  <ContextMenuRadioItem
                    key={member.user_id}
                    value={member.user_id}
                    className="text-xs"
                    onClick={() => patchAssignee(member.user_id)}
                  >
                    <UserAvatar
                      alt={member.first_name || member.email}
                      email={member.email}
                      firstName={member.first_name}
                      className="mr-2 size-4"
                      fallbackClassName="text-[10px]"
                    />
                    <span className="truncate">{member.email}</span>
                  </ContextMenuRadioItem>
                ))}
              </ContextMenuRadioGroup>
            </ContextMenuSubContent>
          </ContextMenuSub>
          <ContextMenuSub>
            <ContextMenuSubTrigger className="text-xs">
              <SignalHigh className="mr-2 size-3.5" />
              Set priority
            </ContextMenuSubTrigger>
            <ContextMenuSubContent className="w-40">
              <ContextMenuRadioGroup value={priority}>
                {Object.values(PRIORITIES).map((priority) => {
                  const Icon = priority.icon
                  return (
                    <ContextMenuRadioItem
                      key={priority.value}
                      value={priority.value}
                      className="text-xs"
                      onClick={() => patchPriority(priority.value)}
                    >
                      <Icon
                        className={cn(
                          "mr-2 size-3.5",
                          priorityIconTone(priority.value)
                        )}
                      />
                      {priority.label}
                    </ContextMenuRadioItem>
                  )
                })}
              </ContextMenuRadioGroup>
            </ContextMenuSubContent>
          </ContextMenuSub>
          <ContextMenuSub>
            <ContextMenuSubTrigger className="text-xs">
              <Workflow className="mr-2 size-3.5" />
              Set workflow
            </ContextMenuSubTrigger>
            <ContextMenuSubContent className="w-56">
              <ContextMenuRadioGroup value={workflowId ?? NO_WORKFLOW}>
                <ContextMenuRadioItem
                  value={NO_WORKFLOW}
                  className="text-xs"
                  onClick={() => patchWorkflow(null)}
                >
                  <CircleSlash className="mr-2 size-3 text-muted-foreground/50" />
                  No workflow
                </ContextMenuRadioItem>
                {workflows.map((item) => (
                  <ContextMenuRadioItem
                    key={item.id}
                    value={item.id}
                    className="text-xs"
                    onClick={() => patchWorkflow(item.id)}
                  >
                    <span className="truncate">
                      {item.title}
                      {item.alias && (
                        <span className="ml-1 italic text-muted-foreground">
                          ({item.alias})
                        </span>
                      )}
                    </span>
                  </ContextMenuRadioItem>
                ))}
              </ContextMenuRadioGroup>
            </ContextMenuSubContent>
          </ContextMenuSub>
          <ContextMenuSeparator />
          <ContextMenuItem
            className="text-xs text-red-600 focus:text-red-600 dark:text-red-400 dark:focus:text-red-400"
            onClick={() => onDelete(task)}
          >
            <Trash2 className="mr-2 size-3.5" />
            Delete
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
      {resolvedWorkflow && (
        <WorkflowTriggerDialog
          caseData={caseData}
          workflowId={resolvedWorkflow.id}
          workflowTitle={resolvedWorkflow.title}
          taskId={task.id}
          defaultTriggerValues={task.default_trigger_values}
          open={runDialogOpen}
          onOpenChange={setRunDialogOpen}
        />
      )}
    </>
  )
}
