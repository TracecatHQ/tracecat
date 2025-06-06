import { useCallback, useState } from "react"
import Link from "next/link"
import {
  TriggerType,
  WorkflowExecutionEventCompact,
  WorkflowExecutionEventStatus,
  WorkflowExecutionReadCompact,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon, QuestionMarkIcon } from "@radix-ui/react-icons"
import {
  AlarmClockCheckIcon,
  AlarmClockOffIcon,
  AlarmClockPlusIcon,
  CalendarIcon,
  CalendarSearchIcon,
  CircleCheck,
  CircleCheckBigIcon,
  CircleDot,
  CircleMinusIcon,
  CirclePlayIcon,
  CircleX,
  EyeOffIcon,
  GitForkIcon,
  LayoutListIcon,
  LoaderIcon,
  ScanEyeIcon,
  SquareArrowOutUpRightIcon,
  UserIcon,
  WebhookIcon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"

import { executionId, groupEventsByActionRef } from "@/lib/event-history"
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
      {/* Trigger type */}
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-2">
          <ZapIcon className="size-3" />
          <span>Trigger type</span>
        </div>
        <div className="ml-auto">
          <Tooltip delayDuration={500}>
            <TooltipTrigger>
              <Badge
                variant="secondary"
                className="flex items-center gap-1 text-foreground/70"
              >
                {getTriggerTypeIcon(execution.trigger_type)}
                <span>
                  {execution.trigger_type.charAt(0).toUpperCase() +
                    execution.trigger_type.slice(1)}
                </span>
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
          <CircleDot className="size-3" />
          <span>Status</span>
        </div>
        <div className="ml-auto">
          <Tooltip delayDuration={500}>
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
    sidebarRef,
  } = useWorkflowBuilder()
  const { workflow } = useWorkflow()
  const [isOpen, setIsOpen] = useState(true)

  // Group events by action_ref
  const groupedEvents = groupEventsByActionRef(events)

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

  const getAggregateStatus = useCallback(
    (relatedEvents: WorkflowExecutionEventCompact[]) => {
      const statuses = relatedEvents.map((event) => event.status)

      // Prioritize showing error states
      if (statuses.some((status) => status === "FAILED")) return "FAILED"
      if (statuses.some((status) => status === "TIMED_OUT")) return "TIMED_OUT"
      if (statuses.some((status) => status === "CANCELED")) return "CANCELED"
      if (statuses.some((status) => status === "TERMINATED"))
        return "TERMINATED"

      // Then show active states
      if (statuses.some((status) => status === "STARTED")) return "STARTED"
      if (statuses.some((status) => status === "SCHEDULED")) return "SCHEDULED"

      // Finally, completed states
      if (statuses.every((status) => status === "COMPLETED")) return "COMPLETED"
      if (statuses.some((status) => status === "DETACHED")) return "DETACHED"

      return "UNKNOWN"
    },
    []
  )

  const getLatestStartTime = useCallback(
    (relatedEvents: WorkflowExecutionEventCompact[]) => {
      const times = relatedEvents
        .map((event) => event.start_time)
        .filter(Boolean)
        .sort()
      return times.length > 0 ? times[times.length - 1] : null
    },
    []
  )

  return (
    <ScrollArea className="p-4 pt-0">
      <div className="pointer-events-none mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <CalendarSearchIcon className="size-3" />
          <span>Events</span>
        </div>
      </div>

      {isOpen && (
        <div className="overflow-hidden rounded-md border">
          <div className="relative">
            {Object.keys(groupedEvents).length > 0 ? (
              Object.entries(groupedEvents).map(
                ([actionRef, relatedEvents]) => {
                  const aggregateStatus = getAggregateStatus(relatedEvents)
                  const latestStartTime = getLatestStartTime(relatedEvents)
                  const instanceCount = relatedEvents.length

                  return (
                    <div key={actionRef}>
                      {/* Vertical timeline line */}
                      <div className="absolute inset-y-5 left-[20px] w-px bg-gray-300" />
                      <div
                        className={cn(
                          "group flex h-9 cursor-pointer items-center border-b border-muted/30 p-3 text-xs transition-all last:border-b-0 hover:bg-muted/50",
                          selectedActionEventRef === actionRef &&
                            "bg-muted-foreground/10"
                        )}
                        onClick={() => handleRowClick(actionRef)}
                      >
                        <div className="relative z-10 mr-3 rounded-full bg-background transition-all group-hover:bg-muted/50">
                          <WorkflowEventStatusIcon status={aggregateStatus} />
                        </div>

                        <div className="flex flex-1 items-center justify-between">
                          <div className="flex items-center gap-2">
                            <div className="truncate text-foreground/70">
                              {actionRef}
                            </div>
                            {instanceCount > 1 && (
                              <Badge
                                variant="secondary"
                                className="h-4 px-1.5 text-[10px] font-medium text-foreground/60"
                              >
                                {instanceCount}x
                              </Badge>
                            )}
                          </div>

                          <div className="flex items-center gap-2">
                            <div className="whitespace-nowrap text-foreground/70">
                              {latestStartTime
                                ? new Date(latestStartTime).toLocaleTimeString()
                                : "-"}
                            </div>

                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  className="size-4 p-0 focus-visible:ring-0"
                                  variant="ghost"
                                >
                                  <DotsHorizontalIcon className="size-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent
                                className={cn(
                                  "flex flex-col",
                                  "[&_[data-radix-collection-item]]:flex",
                                  "[&_[data-radix-collection-item]]:items-center",
                                  "[&_[data-radix-collection-item]]:gap-2",
                                  "[&_[data-radix-collection-item]]:text-xs",
                                  "[&_[data-radix-collection-item]]:text-foreground/80"
                                )}
                              >
                                <DropdownMenuItem
                                  disabled={!isActionRefValid(actionRef)}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    sidebarRef.current?.setOpen(true)
                                    sidebarRef.current?.setActiveTab(
                                      "action-input"
                                    )
                                    setSelectedActionEventRef(
                                      slugify(actionRef)
                                    )
                                  }}
                                >
                                  <LayoutListIcon className="size-3" />
                                  <span>View last input</span>
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  disabled={!isActionRefValid(actionRef)}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    sidebarRef.current?.setOpen(true)
                                    sidebarRef.current?.setActiveTab(
                                      "action-result"
                                    )
                                    setSelectedActionEventRef(
                                      slugify(actionRef)
                                    )
                                  }}
                                >
                                  <CircleCheckBigIcon className="size-3" />
                                  <span>View last result</span>
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  disabled={!isActionRefValid(actionRef)}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    centerNode(actionRef)
                                  }}
                                >
                                  {!isActionRefValid(actionRef) ? (
                                    <EyeOffIcon className="size-3" />
                                  ) : (
                                    <ScanEyeIcon className="size-3" />
                                  )}
                                  <span>Focus action</span>
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                }
              )
            ) : (
              <div className="flex h-16 items-center justify-center bg-muted-foreground/5 p-3 text-center text-xs text-muted-foreground">
                <div className="flex items-center justify-center gap-2">
                  <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
                  <span>Waiting for events...</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
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
      return <Spinner className={cn("!size-3", className)} />
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
          className={cn("!size-3 stroke-rose-500", className)}
          strokeWidth={2.5}
        />
      )
    case "DETACHED":
      return (
        <GitForkIcon
          className={cn("!size-3 stroke-emerald-500", className)}
          strokeWidth={2.5}
        />
      )
    case "UNKNOWN":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    default:
      throw new Error("Invalid status")
  }
}

export function getTriggerTypeIcon(
  triggerType: TriggerType,
  className?: string
) {
  switch (triggerType) {
    case "manual":
      return (
        <div className="relative rounded-full bg-blue-400">
          <UserIcon
            className={cn("size-3 scale-[0.8] stroke-white", className)}
            strokeWidth={2.5}
          />
        </div>
      )
    case "scheduled":
      return (
        <div className="relative rounded-full bg-amber-500">
          <CalendarSearchIcon
            className={cn("size-3 scale-[0.7] stroke-white", className)}
            strokeWidth={2.5}
          />
        </div>
      )
    case "webhook":
      return (
        <div className="relative rounded-full bg-purple-400">
          <WebhookIcon
            className={cn("size-3 scale-[0.7] stroke-white", className)}
            strokeWidth={2.5}
          />
        </div>
      )
    default:
      console.error(`Unknown trigger type: ${triggerType}`)
      return (
        <QuestionMarkIcon className={cn("size-3 text-gray-600", className)} />
      )
  }
}
