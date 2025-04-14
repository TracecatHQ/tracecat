import { useCallback } from "react"
import Link from "next/link"
import {
  WorkflowExecutionEventCompact,
  WorkflowExecutionEventStatus,
  WorkflowExecutionReadCompact,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  AlarmClockCheckIcon,
  AlarmClockOffIcon,
  AlarmClockPlusIcon,
  CalendarIcon,
  CalendarSearchIcon,
  CircleCheck,
  CircleDot,
  CircleMinusIcon,
  CirclePlayIcon,
  CircleX,
  EyeOffIcon,
  LoaderIcon,
  ScanEyeIcon,
  SquareArrowOutUpRightIcon,
  WorkflowIcon,
} from "lucide-react"

import { executionId } from "@/lib/event-history"
import { cn, slugify, undoSlugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { Spinner } from "@/components/loading/spinner"

export function WorkflowEventsHeader({
  execution,
}: {
  execution: WorkflowExecutionReadCompact
}) {
  const { setSelectedNodeId } = useWorkflowBuilder()
  const { workspaceId } = useWorkspace()
  const parentExec = execution.parent_wf_exec_id
  const parentExecId = parentExec ? executionId(parentExec) : null
  return (
    <div className="space-y-2 p-4 text-xs text-muted-foreground">
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-2">
          <CircleDot className="size-3" />
          <span>Status</span>
        </div>
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
          <CalendarIcon className="size-3" />
          <span>Scheduled</span>
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
          <AlarmClockPlusIcon className="size-3" />
          <span>Start time</span>
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
          <AlarmClockCheckIcon className="size-3" />
          <span>End time</span>
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
              <CirclePlayIcon className="size-3" />
              <span>Parent run</span>
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
              <WorkflowIcon className="size-3" />
              <span>Parent workflow</span>
            </div>
            <Badge variant="outline" className="ml-auto text-foreground/70">
              <Link
                href={`/workspaces/${workspaceId}/workflows/${parentExecId.wf}`}
                onClick={() => {
                  setSelectedNodeId(null)
                }}
              >
                <Tooltip>
                  <TooltipTrigger>
                    <div className="flex items-center gap-1">
                      <span className="font-normal">Go to workflow</span>
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
  const {
    selectedActionEventRef,
    setSelectedActionEventRef,
    setNodes,
    canvasRef,
  } = useWorkflowBuilder()
  const { workflow } = useWorkflow()

  const centerNode = useCallback(
    (actionRef: string) => {
      const action = Object.values(workflow?.actions || {}).find(
        (act) => slugify(act.title) === actionRef
      )
      const id = action?.id
      if (id) {
        setNodes((nodes) =>
          nodes.map((node) => ({
            ...node,
            selected: Boolean(node.id === action.id),
          }))
        )
        canvasRef.current?.centerOnNode(id)
      }
    },
    [workflow?.actions, setNodes, canvasRef]
  )
  const handleRowClick = useCallback(
    (actionRef: string) => {
      if (selectedActionEventRef === actionRef) {
        setSelectedActionEventRef(undefined)
      } else {
        setSelectedActionEventRef(actionRef)
      }
    },
    [selectedActionEventRef, setSelectedActionEventRef]
  )

  const isActionRefValid = useCallback(
    (actionRef: string) => {
      const action = Object.values(workflow?.actions || {}).find(
        (act) => slugify(act.title) === actionRef
      )
      return action !== undefined
    },
    [workflow]
  )

  return (
    <ScrollArea className="p-4 pt-0">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        <CalendarSearchIcon className="size-3" />
        <span>Events</span>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="h-8 text-xs">Status</TableHead>
              <TableHead className="h-8 truncate text-xs">Action</TableHead>
              <TableHead className="h-8 text-xs">Start time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.length > 0 ? (
              events.map((event) => (
                <TableRow
                  key={event.source_event_id}
                  className={cn(
                    "cursor-pointer hover:bg-muted/50",
                    selectedActionEventRef === event.action_ref &&
                      "bg-muted-foreground/10"
                  )}
                  onClick={() => handleRowClick(event.action_ref)}
                >
                  <TableCell className="p-0 text-xs font-medium">
                    <div className="flex size-full items-center justify-start pl-4">
                      <WorkflowEventStatusIcon status={event.status} />
                    </div>
                  </TableCell>
                  <TableCell className="max-w-28 text-xs text-foreground/70">
                    <div className="truncate">{event.action_ref}</div>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs">
                    <Badge
                      variant="secondary"
                      className="font-normal text-foreground/70"
                    >
                      {event.start_time
                        ? new Date(event.start_time).toLocaleTimeString()
                        : "-"}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-w-12 text-xs">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button className="size-4 p-0" variant="ghost">
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent>
                        <DropdownMenuItem
                          // Disable if the action reference is not present in the current workflow
                          disabled={!isActionRefValid(event.action_ref)}
                          onClick={(e) => {
                            e.stopPropagation()
                            centerNode(event.action_ref)
                          }}
                          className="flex items-center gap-2 text-xs"
                        >
                          {!isActionRefValid(event.action_ref) ? (
                            <EyeOffIcon className="size-4" />
                          ) : (
                            <ScanEyeIcon className="size-4" />
                          )}
                          <span>Focus action</span>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
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
      return <Spinner className={className} />
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
