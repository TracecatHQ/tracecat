import { useCallback, useMemo } from "react"
import Link from "next/link"
import {
  WorkflowExecutionEventCompact,
  WorkflowExecutionEventStatus,
  WorkflowExecutionReadCompact,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import {
  AlarmClockCheckIcon,
  AlarmClockOffIcon,
  AlarmClockPlusIcon,
  CalendarIcon,
  CircleCheck,
  CircleDot,
  CircleMinusIcon,
  CirclePlayIcon,
  CircleX,
  Loader2,
  LoaderIcon,
  SquareArrowOutUpRightIcon,
  WorkflowIcon,
} from "lucide-react"

import { executionId } from "@/lib/event-history"
import { cn, slugify, undoSlugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getExecutionStatusIcon } from "@/components/executions/nav"

export function WorkflowEventsHeader({
  execution,
}: {
  execution: WorkflowExecutionReadCompact
}) {
  const { workspaceId } = useWorkspace()
  const parentExec = execution.parent_wf_exec_id
  const parentExecId = parentExec ? executionId(parentExec) : null
  return (
    <div className="space-y-2 p-4 text-xs text-muted-foreground">
      <div className="flex items-center gap-2 pb-2">
        <CircleDot className="size-4" />
        <span className="font-semibold">Status</span>
        <div className="ml-auto">
          <Tooltip>
            <TooltipTrigger>
              <Badge
                variant="secondary"
                className="ml-auto flex items-center gap-1 text-foreground/70 hover:cursor-default"
              >
                {getExecutionStatusIcon(execution.status, "size-4")}
                {undoSlugify(execution.status.toLowerCase())}
              </Badge>
            </TooltipTrigger>
            <TooltipContent side="top" className="font-mono tracking-tight">
              {execution.id}
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-2">
          <CalendarIcon className="size-4" />
          <span className="font-semibold">Scheduled</span>
        </div>
        <Badge
          variant="secondary"
          className="ml-auto font-normal text-foreground/60"
        >
          {new Date(execution.start_time).toLocaleString()}
        </Badge>
      </div>
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-2">
          <AlarmClockPlusIcon className="size-4" />
          <span className="font-semibold">Started</span>
        </div>
        <Badge
          variant="secondary"
          className="ml-auto font-normal text-foreground/60"
        >
          {execution.execution_time
            ? new Date(execution.execution_time).toLocaleString()
            : "..."}
        </Badge>
      </div>
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-2">
          <AlarmClockCheckIcon className="size-4" />
          <span className="font-semibold">Completed</span>
        </div>
        <Badge
          variant="secondary"
          className="ml-auto font-normal text-foreground/60"
        >
          {execution.close_time
            ? new Date(execution.close_time).toLocaleString()
            : "..."}
        </Badge>
      </div>

      {parentExecId && (
        <>
          <div className="flex items-center gap-1">
            <div className="flex items-center gap-2">
              <CirclePlayIcon className="size-4" />
              <span className="font-semibold">Parent Run</span>
            </div>
            <Badge variant="outline" className="ml-auto text-foreground/70">
              <Link
                href={`/workspaces/${workspaceId}/workflows/${parentExecId.wf}/executions/${parentExecId.exec}`}
              >
                <Tooltip>
                  <TooltipTrigger>
                    <div className="flex items-center gap-1">
                      <span className="font-normal">View detailed run</span>
                      <SquareArrowOutUpRightIcon className="size-3" />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    <span>{parentExecId.exec}</span>
                  </TooltipContent>
                </Tooltip>
              </Link>
            </Badge>
          </div>
          <div className="flex items-center gap-1">
            <div className="flex items-center gap-2">
              <WorkflowIcon className="size-4" />
              <span className="font-semibold">Parent Workflow</span>
            </div>
            <Badge variant="outline" className="ml-auto text-foreground/70">
              <Link
                href={`/workspaces/${workspaceId}/workflows/${parentExecId.wf}`}
              >
                <Tooltip>
                  <TooltipTrigger>
                    <div className="flex items-center gap-1">
                      <span className="font-normal">Go to builder</span>
                      <SquareArrowOutUpRightIcon className="size-3" />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    <span>{parentExecId.wf}</span>
                  </TooltipContent>
                </Tooltip>
              </Link>
            </Badge>
          </div>
        </>
      )}
    </div>
  )
}
export function WorkflowEvents({
  events,
}: {
  events: WorkflowExecutionEventCompact[]
}) {
  const { selectedNodeId, setSelectedNodeId, setNodes, canvasRef } =
    useWorkflowBuilder()
  const { workflow } = useWorkflow()

  const handleRowClick = useCallback(
    (actionRef: string) => {
      setSelectedNodeId(actionRef)
      const action = Object.values(workflow?.actions || {}).find(
        (act) => slugify(act.title) === actionRef
      )
      if (action) {
        console.log("action", action, canvasRef.current)
        const id = action.id
        setSelectedNodeId(id)
        setNodes((nodes) =>
          nodes.map((node) => ({ ...node, selected: Boolean(node.id === id) }))
        )
        canvasRef.current?.centerOnNode(id)
      }
    },
    [setSelectedNodeId, workflow, canvasRef.current]
  )

  const selectedNodeRef = useMemo(() => {
    return selectedNodeId
      ? slugify(workflow?.actions[selectedNodeId]?.title || "")
      : null
  }, [selectedNodeId, workflow])

  return (
    <ScrollArea className="p-4 pt-0">
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="h-8 w-[100px] text-xs font-semibold">
                Status
              </TableHead>
              <TableHead className="h-8 text-xs font-semibold">
                Action Reference
              </TableHead>
              <TableHead className="h-8 text-xs font-semibold">
                Start Time
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.length > 0 ? (
              events.map((event) => (
                <TableRow
                  key={event.source_event_id}
                  className={cn(
                    "cursor-pointer hover:bg-muted/50",
                    selectedNodeRef === event.action_ref &&
                      "bg-muted-foreground/10"
                  )}
                  onClick={() => handleRowClick(event.action_ref)}
                >
                  <TableCell className="p-0 text-xs font-medium">
                    <div className="flex size-full items-center justify-center py-3">
                      <WorkflowEventStatusIcon status={event.status} />
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-foreground/70">
                    {event.action_ref}
                  </TableCell>
                  <TableCell className="text-xs">
                    <Badge
                      variant="secondary"
                      className="font-normal text-foreground/70"
                    >
                      {event.start_time
                        ? new Date(event.start_time).toLocaleTimeString()
                        : "-"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))
            ) : (
              <TableRow className="justify-center text-xs text-muted-foreground">
                <TableCell
                  className="h-16 items-center justify-center bg-muted-foreground/5 text-center"
                  colSpan={3}
                >
                  <div className="flex items-center justify-center gap-2">
                    <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
                    <span>Waiting for events...</span>
                  </div>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </ScrollArea>
  )
}
export function WorkflowEventStatusIcon({
  status,
  className = "size-4",
}: {
  status: WorkflowExecutionEventStatus
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {getWorkflowEventIcon(status, className)}
      </TooltipTrigger>
      <TooltipContent side="top" className="flex items-center gap-4  shadow-lg">
        <span>{undoSlugify(status.toLowerCase())}</span>
      </TooltipContent>
    </Tooltip>
  )
}
export function getWorkflowEventIcon(
  status: WorkflowExecutionEventStatus,
  className?: string
) {
  switch (status) {
    case "SCHEDULED":
      return (
        <LoaderIcon
          className={cn(
            "animate-spin stroke-orange-500/50 [animation-duration:4s]",
            className
          )}
        />
      )
    case "STARTED":
      return (
        <Loader2 className={cn("animate-spin stroke-blue-500/50", className)} />
      )
    case "COMPLETED":
      return (
        <CircleCheck
          className={cn(
            "border-none border-emerald-500 fill-emerald-500 stroke-white",
            className
          )}
        />
      )
    case "FAILED":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    case "CANCELED":
      return (
        <CircleMinusIcon
          className={cn("fill-orange-500 stroke-white", className)}
        />
      )
    case "TERMINATED":
      return (
        <CircleMinusIcon
          className={cn("fill-rose-500 stroke-white", className)}
        />
      )
    case "TIMED_OUT":
      return (
        <AlarmClockOffIcon
          className={cn("stroke-rose-500", className)}
          strokeWidth={2.5}
        />
      )
    default:
      throw new Error("Invalid status")
  }
}
