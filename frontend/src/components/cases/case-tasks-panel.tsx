"use client"

import { useMemo, useState } from "react"
import type { CaseRead, CaseTaskRead, WorkflowReadMinimal } from "@/client"
import {
  AddTaskRow,
  CaseTaskComposer,
} from "@/components/cases/case-task-composer"
import { CaseTaskRow } from "@/components/cases/case-task-row"
import { DeleteCaseTaskDialog } from "@/components/cases/delete-case-task-dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { useCaseTasks, useWorkflowManager } from "@/lib/hooks"
import { cn, INSET_SURFACE } from "@/lib/utils"

/**
 * Editor-slot key for the new-task card. Task ids are UUIDs, so this can
 * never collide with one.
 */
const NEW_TASK_EDITOR = "__new_task__"

/**
 * The list's box: the same recessed, `border-border/60` card the comment
 * threads below the panel use, at the same full body width, so tasks and
 * comments read as two boxes on one column rather than two systems. `p-2`
 * around rows whose own `px-3` lands their text 20px in — the comment cards'
 * `px-5` inset — while leaving the hover pill inset from the border.
 */
const CASE_TASKS_CONTAINER_CLASS = cn(
  "overflow-hidden rounded-lg border border-border/60 p-2",
  INSET_SURFACE
)

/** Props for {@link CaseTasksPanel}. Identical to the old `CaseTasksSection`. */
export interface CaseTasksPanelProps {
  caseId: string
  workspaceId: string
  caseData: CaseRead
}

/**
 * The Tasks panel: a flush stack of one-line task rows, Linear's sub-issue
 * list. No header and no accordion — the switcher button above is the header
 * and carries the progress ring and the `done/total` count. The `+ Add task`
 * ghost row doubles as the empty state and swaps in place for the composer.
 *
 * The panel owns which row is in edit mode — at most one, tracked as either
 * a task id or the new-task slot — so opening one composer closes any other.
 * The only dialog hosted here is the delete confirm, reachable from each
 * row's `⋯` menu and context menu.
 */
export function CaseTasksPanel({
  caseId,
  workspaceId,
  caseData,
}: CaseTasksPanelProps) {
  const { caseTasks, caseTasksIsLoading, caseTasksError } = useCaseTasks({
    caseId,
    workspaceId,
  })
  const { workflows } = useWorkflowManager()
  const { members } = useWorkspaceMembers(workspaceId)
  // The one card allowed in edit mode: a task id, the new-task slot, or none.
  const [activeEditor, setActiveEditor] = useState<string | null>(null)
  // Two-state dialog pattern: `taskPendingDelete` is retained after `open`
  // goes false so the confirm keeps its content through the exit animation
  // instead of blanking mid-fade.
  const [taskPendingDelete, setTaskPendingDelete] =
    useState<CaseTaskRead | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const workflowById = useMemo(() => {
    const map = new Map<string, WorkflowReadMinimal>()
    workflows?.forEach((workflow) => map.set(workflow.id, workflow))
    return map
  }, [workflows])

  function handleDeleteTask(task: CaseTaskRead) {
    setTaskPendingDelete(task)
    setDeleteDialogOpen(true)
  }

  /**
   * Closes one editor slot without clobbering another: a late close (a save
   * resolving after the user already opened a different row's composer) must
   * not shut the newer composer.
   */
  function closeEditor(editor: string) {
    setActiveEditor((current) => (current === editor ? null : current))
  }

  if (caseTasksIsLoading) {
    return (
      <div className={CASE_TASKS_CONTAINER_CLASS}>
        {[...Array(3)].map((_, index) => (
          <Skeleton key={index} className="mx-2 my-1 h-7 rounded-md" />
        ))}
      </div>
    )
  }

  if (caseTasksError) {
    return (
      <div className={CASE_TASKS_CONTAINER_CLASS}>
        <p className="px-3 py-2 text-sm text-muted-foreground">
          Failed to load tasks
        </p>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={300}>
      {/* Rows are 40px lines with 2px between them: enough air that adjacent
          hover pills read as separate targets, not enough to break the list
          into cards. */}
      <div className={cn(CASE_TASKS_CONTAINER_CLASS, "flex flex-col gap-0.5")}>
        {(caseTasks ?? []).map((task) => (
          // Keyed by task.id, never index: `WorkflowTriggerDialog` renders as
          // a child of the row, and an index key would remount it — closing
          // the dialog mid-flow — whenever a refetch reorders the list.
          <CaseTaskRow
            key={task.id}
            task={task}
            editing={activeEditor === task.id}
            workflow={
              task.workflow_id
                ? (workflowById.get(task.workflow_id) ?? null)
                : null
            }
            workflows={workflows ?? []}
            members={members ?? []}
            caseData={caseData}
            caseId={caseId}
            workspaceId={workspaceId}
            onRequestEdit={(target) => setActiveEditor(target.id)}
            onCloseEdit={() => closeEditor(task.id)}
            onDelete={handleDeleteTask}
          />
        ))}
        {activeEditor === NEW_TASK_EDITOR ? (
          <CaseTaskComposer
            task={null}
            caseId={caseId}
            workspaceId={workspaceId}
            workflows={workflows ?? []}
            members={members ?? []}
            onClose={() => closeEditor(NEW_TASK_EDITOR)}
          />
        ) : (
          <AddTaskRow onClick={() => setActiveEditor(NEW_TASK_EDITOR)} />
        )}
      </div>
      <DeleteCaseTaskDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        task={taskPendingDelete}
        caseId={caseId}
        workspaceId={workspaceId}
      />
    </TooltipProvider>
  )
}
