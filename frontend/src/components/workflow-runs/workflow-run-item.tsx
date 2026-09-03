"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { format, formatDistanceToNow } from "date-fns"
import {
  Check,
  CircleCheck,
  CircleDot,
  CircleHelpIcon,
  FlagTriangleRight,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type {
  WorkflowExecutionReadMinimal,
  WorkflowRunReadMinimal,
} from "@/client"
import { WorkflowExecutionStatusIcon } from "@/components/executions/nav"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { parseExecutionId } from "@/lib/event-history"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

interface WorkflowRunItemProps {
  run: WorkflowRunReadMinimal
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  onCancel: (executionId: string) => void
  onTerminate: (executionId: string) => void
  onReset: (executionId: string) => void
}

const SUPPORTED_STATUS_ICONS = new Set<WorkflowExecutionReadMinimal["status"]>([
  "RUNNING",
  "COMPLETED",
  "FAILED",
  "CANCELED",
  "TERMINATED",
  "CONTINUED_AS_NEW",
  "TIMED_OUT",
])

function getRelativeDateLabel(dateValue: string): string {
  const timestamp = new Date(dateValue).getTime()
  if (Number.isNaN(timestamp)) {
    return "-"
  }
  return formatDistanceToNow(new Date(dateValue), { addSuffix: true })
}

export function WorkflowRunItem({
  run,
  checked,
  onCheckedChange,
  onCancel,
  onTerminate,
  onReset,
}: WorkflowRunItemProps) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const [terminateConfirmOpen, setTerminateConfirmOpen] = useState(false)
  let executionSuffix: string | null = null
  try {
    const parsed = parseExecutionId(run.id)
    executionSuffix = parsed[1]
  } catch {
    executionSuffix = null
  }
  const workflowId = run.workflow_id

  const detailPath =
    workflowId && executionSuffix
      ? `/workspaces/${workspaceId}/workflows/${workflowId}/executions/${executionSuffix}`
      : null
  const workflowPath = workflowId
    ? `/workspaces/${workspaceId}/workflows/${workflowId}`
    : null

  const statusIcon = SUPPORTED_STATUS_ICONS.has(run.status) ? (
    <WorkflowExecutionStatusIcon status={run.status} className="size-4" />
  ) : (
    <CircleHelpIcon className="size-4 text-muted-foreground" />
  )
  const isRunning = run.status === "RUNNING"

  return (
    <div
      className={cn(
        "group/item -ml-[18px] flex w-[calc(100%+18px)] items-center gap-2 py-2 pl-3 pr-3 text-left transition-colors",
        "hover:bg-muted/50",
        checked && "bg-muted/30",
        detailPath && "cursor-pointer"
      )}
      onClick={() => {
        if (detailPath) {
          router.push(detailPath)
        }
      }}
    >
      <button
        type="button"
        className="flex h-7 w-7 shrink-0 items-center justify-center"
        onClick={(event) => {
          event.stopPropagation()
          onCheckedChange(!checked)
        }}
        role="checkbox"
        aria-checked={checked}
        aria-label={`Select run ${run.id}`}
      >
        <span
          className={cn(
            "flex size-4 shrink-0 items-center justify-center rounded-sm border transition-colors",
            !checked && "opacity-0 group-hover/item:opacity-100",
            checked
              ? "border-primary bg-primary text-primary-foreground"
              : "border-muted-foreground/40 bg-transparent"
          )}
        >
          {checked && <Check className="size-3" aria-hidden />}
        </span>
      </button>

      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {statusIcon}
          {workflowId ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-xs font-medium">
                    {run.workflow_title ?? "Unknown workflow"}
                  </span>
                  {run.workflow_alias ? (
                    <span className="truncate text-[10px] text-muted-foreground">
                      @{run.workflow_alias}
                    </span>
                  ) : null}
                </div>
              </TooltipTrigger>
              <TooltipContent>
                <span>Workflow ID: {workflowId}</span>
              </TooltipContent>
            </Tooltip>
          ) : (
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-xs font-medium">
                {run.workflow_title ?? "Unknown workflow"}
              </span>
              {run.workflow_alias ? (
                <span className="truncate text-[10px] text-muted-foreground">
                  @{run.workflow_alias}
                </span>
              ) : null}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="secondary"
                className="h-5 cursor-default px-2 text-[10px] font-normal"
              >
                <FlagTriangleRight className="mr-1 size-3" />
                {getRelativeDateLabel(run.start_time)}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              Started {format(new Date(run.start_time), "PPpp")}
            </TooltipContent>
          </Tooltip>
          {run.close_time ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  variant="secondary"
                  className="h-5 cursor-default px-2 text-[10px] font-normal"
                >
                  <CircleCheck className="mr-1 size-3" />
                  {getRelativeDateLabel(run.close_time)}
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                Ended {format(new Date(run.close_time), "PPpp")}
              </TooltipContent>
            </Tooltip>
          ) : (
            <Badge
              variant="secondary"
              className="h-5 px-2 text-[10px] font-normal"
            >
              <CircleDot className="mr-1 size-3" />
              Open
            </Badge>
          )}
        </div>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            className="size-7 p-0 text-muted-foreground opacity-0 transition-opacity group-hover/item:opacity-100 data-[state=open]:opacity-100"
            onClick={(event) => event.stopPropagation()}
          >
            <span className="sr-only">Open run actions</span>
            <DotsHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {workflowPath ? (
            <DropdownMenuItem asChild className="text-xs">
              <Link
                href={workflowPath}
                onClick={(event) => {
                  event.stopPropagation()
                }}
              >
                View workflow
              </Link>
            </DropdownMenuItem>
          ) : null}
          {isRunning ? (
            <DropdownMenuItem
              className="text-xs"
              onClick={(event) => {
                event.stopPropagation()
                onCancel(run.id)
              }}
            >
              Cancel
            </DropdownMenuItem>
          ) : null}
          {isRunning ? (
            <DropdownMenuItem
              className="text-xs text-rose-500 focus:text-rose-600"
              onClick={(event) => {
                event.stopPropagation()
                setTerminateConfirmOpen(true)
              }}
            >
              Terminate
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-xs"
            onClick={(event) => {
              event.stopPropagation()
              onReset(run.id)
            }}
          >
            Reset
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog
        open={terminateConfirmOpen}
        onOpenChange={(nextOpen) => setTerminateConfirmOpen(nextOpen)}
      >
        <AlertDialogContent onClick={(event) => event.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Terminate workflow run</AlertDialogTitle>
            <AlertDialogDescription>
              This will immediately terminate the run. This action cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              onClick={(event) => {
                event.stopPropagation()
                setTerminateConfirmOpen(false)
              }}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={(event) => {
                event.stopPropagation()
                setTerminateConfirmOpen(false)
                onTerminate(run.id)
              }}
            >
              Terminate
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
