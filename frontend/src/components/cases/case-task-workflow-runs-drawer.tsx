"use client"

import { formatDistanceToNow } from "date-fns"
import Link from "next/link"
import type { CaseTaskRead, WorkflowRunReadMinimal } from "@/client"
import { CaseValueDrawer } from "@/components/cases/case-value-drawer"
import { WorkflowExecutionStatusIcon } from "@/components/executions/nav"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useCaseTaskWorkflowRuns } from "@/hooks/use-workflow-runs"
import { parseExecutionId } from "@/lib/event-history"
import { undoSlugify } from "@/lib/utils"

/** Props for {@link CaseTaskWorkflowRunsDrawer}. */
export interface CaseTaskWorkflowRunsDrawerProps {
  task: CaseTaskRead
  workspaceId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Every workflow run started from a case task, newest first, in the case
 * page's non-modal left drawer. Each run links to its execution view.
 */
export function CaseTaskWorkflowRunsDrawer({
  task,
  workspaceId,
  open,
  onOpenChange,
}: CaseTaskWorkflowRunsDrawerProps) {
  return (
    <CaseValueDrawer
      open={open}
      onOpenChange={onOpenChange}
      title="Workflow runs"
      description={task.title}
    >
      {open && (
        <CaseTaskWorkflowRunsList taskId={task.id} workspaceId={workspaceId} />
      )}
    </CaseValueDrawer>
  )
}

function CaseTaskWorkflowRunsList({
  taskId,
  workspaceId,
}: {
  taskId: string
  workspaceId: string
}) {
  const {
    runs,
    isLoading,
    error,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useCaseTaskWorkflowRuns({ workspaceId, taskId })

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2 p-4">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <p className="p-4 text-xs text-red-600 dark:text-red-400">
        Failed to load workflow runs: {error.message}
      </p>
    )
  }

  if (runs.length === 0) {
    return (
      <p className="p-4 text-xs text-muted-foreground">
        No workflow runs yet. Runs started from this task show up here.
      </p>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <ul className="flex flex-col py-1">
        {runs.map((run) => (
          <li key={`${run.id}:${run.run_id}`}>
            <WorkflowRunLink run={run} workspaceId={workspaceId} />
          </li>
        ))}
      </ul>
      {hasNextPage && (
        <div className="px-4 pb-4">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-full text-xs"
            disabled={isFetchingNextPage}
            onClick={() => fetchNextPage()}
          >
            {isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </div>
  )
}

function executionHref(
  workspaceId: string,
  run: WorkflowRunReadMinimal
): string | null {
  if (!run.workflow_id) {
    return null
  }
  try {
    const [, executionSuffix] = parseExecutionId(run.id)
    return `/workspaces/${workspaceId}/workflows/${run.workflow_id}/executions/${executionSuffix}`
  } catch {
    return null
  }
}

function startedLabel(startTime: string): string {
  const date = new Date(startTime)
  if (Number.isNaN(date.getTime())) {
    return "-"
  }
  return formatDistanceToNow(date, { addSuffix: true })
}

function WorkflowRunLink({
  run,
  workspaceId,
}: {
  run: WorkflowRunReadMinimal
  workspaceId: string
}) {
  const href = executionHref(workspaceId, run)
  const content = (
    <>
      <WorkflowExecutionStatusIcon
        status={run.status}
        className="size-4 shrink-0"
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-xs font-medium">
          {run.workflow_title ?? "Untitled workflow"}
        </span>
        <span className="truncate text-[11px] text-muted-foreground">
          {undoSlugify(run.status.toLowerCase())} ·{" "}
          {startedLabel(run.start_time)}
        </span>
      </div>
    </>
  )
  const className = "flex items-center gap-3 px-4 py-2"

  if (!href) {
    return <div className={className}>{content}</div>
  }
  return (
    <Link href={href} className={`${className} hover:bg-muted/50`}>
      {content}
    </Link>
  )
}
