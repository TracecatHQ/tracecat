"use client"

import { InfoIcon, LockIcon, LockOpenIcon } from "lucide-react"
import { useMemo } from "react"
import type {
  ActionRead,
  ActionUpdate,
  WorkflowExecutionReadMinimal,
} from "@/client"
import { getExecutionStatusIcon } from "@/components/executions/nav"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useCompactWorkflowExecution, useWorkflowExecutions } from "@/lib/hooks"
import { slugifyActionRef } from "@/lib/utils"

const MAX_RUNS = 25

/** Past runs of an action, with a control to lock draft runs to one of them. */
export function ActionRunHistory({
  action,
  workflowId,
  updateAction,
}: {
  action: ActionRead
  workflowId: string
  updateAction: (values: ActionUpdate) => Promise<unknown>
}) {
  const { workflowExecutions, workflowExecutionsIsLoading } =
    useWorkflowExecutions(workflowId)
  const runs = useMemo(
    () =>
      [...(workflowExecutions ?? [])]
        .sort((a, b) => b.start_time.localeCompare(a.start_time))
        .slice(0, MAX_RUNS),
    [workflowExecutions]
  )
  const ref = slugifyActionRef(action.title)
  const lockedId = action.locked_execution_id ?? null

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex h-7 items-center justify-between gap-4">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Recent runs
              <InfoIcon className="size-3.5" />
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-64">
            Lock to a run and draft runs reuse its result. Published runs ignore
            locks.
          </TooltipContent>
        </Tooltip>
        {lockedId && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 shrink-0 gap-1.5 text-xs"
            onClick={() => updateAction({ locked_execution_id: null })}
          >
            <LockOpenIcon className="size-3.5" />
            Unlock
          </Button>
        )}
      </div>
      <div className="divide-y rounded-md border">
        {workflowExecutionsIsLoading ? (
          <div className="p-3 text-xs text-muted-foreground">Loading runs…</div>
        ) : runs.length === 0 ? (
          <div className="p-3 text-xs text-muted-foreground">
            No runs yet. Run the workflow to lock this action to a result.
          </div>
        ) : (
          runs.map((run) => (
            <ActionRunRow
              key={run.id}
              run={run}
              actionRef={ref}
              locked={run.id === lockedId}
              onLock={() => updateAction({ locked_execution_id: run.id })}
            />
          ))
        )}
      </div>
    </div>
  )
}

function ActionRunRow({
  run,
  actionRef,
  locked,
  onLock,
}: {
  run: WorkflowExecutionReadMinimal
  actionRef: string
  locked: boolean
  onLock: () => void
}) {
  const { execution } = useCompactWorkflowExecution(run.id)
  const event = execution?.events.findLast((e) => e.action_ref === actionRef)
  const reusable = event?.status === "COMPLETED"
  const label = !execution
    ? "Loading…"
    : !event
      ? "Didn't run"
      : event.status.toLowerCase().replace("_", " ")

  return (
    <div className="group flex h-10 items-center gap-3 px-3 text-xs">
      {getExecutionStatusIcon(
        (event?.status ?? run.status) as WorkflowExecutionReadMinimal["status"],
        "size-3.5 shrink-0"
      )}
      <span className="truncate">
        {new Date(run.start_time).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        })}
      </span>
      {run.execution_type === "draft" && (
        <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
          Draft
        </Badge>
      )}
      <span className="ml-auto shrink-0 capitalize text-muted-foreground">
        {label}
      </span>
      {locked ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex size-7 shrink-0 items-center justify-center text-amber-600 dark:text-amber-400">
              <LockIcon className="size-3.5" />
            </span>
          </TooltipTrigger>
          <TooltipContent>Locked to this run</TooltipContent>
        </Tooltip>
      ) : reusable ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-7 shrink-0 opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
              aria-label="Lock to this run"
              onClick={onLock}
            >
              <LockIcon className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Lock to this run</TooltipContent>
        </Tooltip>
      ) : (
        <span className="size-7 shrink-0" />
      )}
    </div>
  )
}
