import { DotsHorizontalIcon, QuestionMarkIcon } from "@radix-ui/react-icons"
import {
  AlarmClockCheckIcon,
  AlarmClockPlusIcon,
  BriefcaseBusinessIcon,
  CalendarIcon,
  CalendarSearchIcon,
  CircleCheckBigIcon,
  CircleDot,
  CirclePlayIcon,
  EyeOffIcon,
  LayoutListIcon,
  LoaderIcon,
  Repeat2Icon,
  ScanEyeIcon,
  SquareArrowOutUpRightIcon,
  UserIcon,
  WebhookIcon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"
import Link from "next/link"
import { useCallback, useState } from "react"
import type { TriggerType, WorkflowExecutionEventStatus } from "@/client"
import {
  getAggregateWorkflowEventStatus,
  getWorkflowEventIcon,
} from "@/components/events/workflow-event-status"
import {
  WorkflowEventsList,
  type WorkflowEventsListRow,
} from "@/components/events/workflow-events-list"
import { getExecutionStatusIcon } from "@/components/executions/nav"
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
import {
  executionId,
  getLatestLoopEventMeta,
  getLoopEventMeta,
  groupEventsByActionRef,
  refToLabel,
  WF_COMPLETED_EVENT_REF,
  WF_FAILURE_EVENT_REF,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { cn, slugifyActionRef, undoSlugify } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspaceId } from "@/providers/workspace-id"

export function WorkflowEventsHeader({
  execution,
}: {
  execution: WorkflowExecutionReadCompact
}) {
  const { setSelectedNodeId } = useWorkflowBuilder()
  const workspaceId = useWorkspaceId()
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
  status,
}: {
  events: WorkflowExecutionEventCompact[]
  status: WorkflowExecutionReadCompact["status"]
}) {
  const {
    selectedActionEventRef,
    setSelectedActionEventRef,
    setNodes,
    canvasRef,
    sidebarRef,
  } = useWorkflowBuilder()
  const { workflow } = useWorkflow()
  const workspaceId = useWorkspaceId()
  const [isOpen, _setIsOpen] = useState(true)

  // Group events by action_ref
  const groupedEvents = groupEventsByActionRef(events)

  const centerNode = useCallback(
    (actionRef: string) => {
      const action = Object.values(workflow?.actions || {}).find(
        (act) => slugifyActionRef(act.title) === actionRef
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
        (act) => slugifyActionRef(act.title) === actionRef
      )
      return action !== undefined
    },
    [workflow]
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

  const getChildWorkflowRunLink = useCallback(
    (relatedEvents: WorkflowExecutionEventCompact[]) => {
      const eventWithChildRun = [...relatedEvents]
        .filter((event) => Boolean(event.child_wf_exec_id))
        .sort((a, b) => {
          const dateA = new Date(a.start_time || a.schedule_time).getTime()
          const dateB = new Date(b.start_time || b.schedule_time).getTime()
          return dateB - dateA
        })[0]

      if (!eventWithChildRun?.child_wf_exec_id) {
        return undefined
      }

      try {
        const childExecution = executionId(eventWithChildRun.child_wf_exec_id)
        return `/workspaces/${workspaceId}/workflows/${childExecution.wf}/executions/${childExecution.exec}`
      } catch {
        return undefined
      }
    },
    [workspaceId]
  )

  const getLatestEvent = useCallback(
    (relatedEvents: WorkflowExecutionEventCompact[]) =>
      [...relatedEvents].sort((a, b) => {
        const dateA = new Date(a.start_time || a.schedule_time).getTime()
        const dateB = new Date(b.start_time || b.schedule_time).getTime()
        return dateB - dateA
      })[0],
    []
  )

  const eventRows = Object.entries(groupedEvents).map(
    ([actionRef, relatedEvents]) => {
      const latestEvent = getLatestEvent(relatedEvents)
      return {
        actionRef,
        relatedEvents,
        latestEvent,
      }
    }
  )
  const rows: WorkflowEventsListRow[] = eventRows.map(
    ({ actionRef, relatedEvents, latestEvent }) => {
      const aggregateStatus = getAggregateWorkflowEventStatus(relatedEvents)
      const latestStartTime = getLatestStartTime(relatedEvents)
      const isLoopAction =
        latestEvent?.action_name === "core.loop.start" ||
        latestEvent?.action_name === "core.loop.end"
      const loopMeta =
        getLoopEventMeta(latestEvent) ?? getLatestLoopEventMeta(relatedEvents)
      const loopBadge =
        latestEvent?.action_name === "core.loop.start" ? loopMeta : undefined
      const loopTooltip =
        latestEvent?.action_name === "core.loop.start" && loopMeta
          ? `Iteration ${loopMeta}`
          : loopMeta
      const instanceCount = relatedEvents.length
      const childWorkflowRunLink = getChildWorkflowRunLink(relatedEvents)

      return {
        key: actionRef,
        label: refToLabel(actionRef),
        meta: loopBadge,
        time: latestStartTime
          ? new Date(latestStartTime).toLocaleTimeString()
          : "-",
        icon: isLoopAction ? (
          loopTooltip ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="relative flex size-5 items-center justify-center">
                  {getWorkflowEventIcon(aggregateStatus, "size-5")}
                  <Repeat2Icon className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-sm bg-orange-100 text-orange-700 ring-1 ring-orange-200" />
                </div>
              </TooltipTrigger>
              <TooltipContent side="top">
                <span>{loopTooltip}</span>
              </TooltipContent>
            </Tooltip>
          ) : (
            <div className="relative flex size-5 items-center justify-center">
              <WorkflowEventStatusIcon status={aggregateStatus} />
              <Repeat2Icon className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-sm bg-orange-100 text-orange-700 ring-1 ring-orange-200" />
            </div>
          )
        ) : (
          <WorkflowEventStatusIcon status={aggregateStatus} />
        ),
        selected: selectedActionEventRef === actionRef,
        count: instanceCount,
        subflowLink: childWorkflowRunLink,
        onSelect: () => handleRowClick(actionRef),
        trailing: (
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
                  sidebarRef.current?.setActiveTab("action-input")
                  setSelectedActionEventRef(actionRef)
                }}
              >
                <LayoutListIcon className="size-3" />
                <span>View last input</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={
                  !isActionRefValid(actionRef) &&
                  actionRef !== WF_FAILURE_EVENT_REF &&
                  actionRef !== WF_COMPLETED_EVENT_REF
                }
                onClick={(e) => {
                  e.stopPropagation()
                  sidebarRef.current?.setOpen(true)
                  sidebarRef.current?.setActiveTab("action-result")
                  setSelectedActionEventRef(actionRef)
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
        ),
      }
    }
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
        <div className="overflow-hidden border">
          {Object.keys(groupedEvents).length > 0 ? (
            <WorkflowEventsList rows={rows} />
          ) : (
            <div className="flex h-16 items-center justify-center bg-muted-foreground/5 p-3 text-center text-xs text-muted-foreground">
              <div className="flex items-center justify-center gap-2">
                {status === "RUNNING" ? (
                  <>
                    <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
                    <span>Waiting for events...</span>
                  </>
                ) : (
                  <>
                    <CircleDot className="size-3 text-muted-foreground" />
                    <span>No events</span>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </ScrollArea>
  )
}
export function WorkflowEventStatusIcon({
  status,
  className = "size-5",
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
    case "case":
      return (
        <div className="relative rounded-full bg-emerald-500">
          <BriefcaseBusinessIcon
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
