"use client"

import React from "react"
import { WorkflowEventType, WorkflowExecutionEvent } from "@/client"
import {
  AlarmClockOffIcon,
  BookCheckIcon,
  CalendarCheckIcon,
  CircleArrowRightIcon,
  CircleCheck,
  CircleDotIcon,
  CircleMinusIcon,
  CircleXIcon,
  PlayIcon,
  WorkflowIcon,
} from "lucide-react"

import { useWorkflowExecution } from "@/lib/hooks"
import { cn, undoSlugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { CenteredSpinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"

import "react18-json-view/src/style.css"

import { ERROR_EVENT_TYPES, parseEventType } from "@/lib/event-history"

const REFETCH_INTERVAL = 2000 // 2 seconds

/**
 * Event history for a specific workflow execution
 * @param param0
 * @returns
 */
export function WorkflowExecutionEventHistory({
  executionId,
  selectedEvent,
  setSelectedEvent,
}: {
  executionId: string
  selectedEvent?: WorkflowExecutionEvent
  setSelectedEvent: (event: WorkflowExecutionEvent) => void
}) {
  const { execution, executionIsLoading, executionError } =
    useWorkflowExecution(executionId, {
      refetchInterval: REFETCH_INTERVAL,
    })

  if (executionIsLoading) {
    return <CenteredSpinner />
  }
  if (executionError) {
    return <AlertNotification message={executionError.message} />
  }
  if (!execution) {
    return <NoContent message="No event history found." />
  }
  return (
    <div className="group flex flex-col gap-4 py-2">
      <nav className="grid gap-1 px-2">
        {execution.events.map((event, index) => (
          <Button
            key={index}
            className={cn(
              buttonVariants({ variant: "default", size: "sm" }),
              "justify-start space-x-1 bg-background text-muted-foreground shadow-none hover:cursor-default hover:bg-gray-100",
              event.event_id === selectedEvent?.event_id && "bg-gray-200",
              ERROR_EVENT_TYPES.includes(event.event_type) &&
                "bg-red-100 hover:bg-red-200"
            )}
            onClick={() => {
              setSelectedEvent(event)
            }}
          >
            <div className="flex items-center justify-items-start">
              <div className="flex w-10">
                <Badge
                  variant="secondary"
                  className="max-w-10 flex-none rounded-md bg-indigo-50 p-1 text-xs font-light text-muted-foreground"
                >
                  {event.event_id}
                </Badge>
              </div>
              <EventHistoryItemIcon
                eventType={event.event_type}
                className="size-4 w-8 flex-none"
              />

              <span className="text-xs text-muted-foreground">
                <EventDescriptor event={event} />
              </span>
            </div>
          </Button>
        ))}
      </nav>
    </div>
  )
}

export function EventDescriptor({
  event,
}: {
  event: WorkflowExecutionEvent
}): React.ReactNode {
  if (event.event_type.startsWith("ACTIVITY_TASK")) {
    return (
      <span className="flex items-center space-x-2 text-xs">
        <span>{event.event_group?.action_title ?? "Unnamed action"}</span>
      </span>
    )
  }
  if (event.event_type.includes("CHILD_WORKFLOW_EXECUTION")) {
    return (
      <span className="flex items-center space-x-2 text-xs">
        <span>{event.event_group?.action_title ?? "Unnamed action"}</span>
      </span>
    )
  }
  return <span className="capitalize">{parseEventType(event.event_type)}</span>
}
export function EventHistoryItemIcon({
  eventType,
  className,
}: {
  eventType: WorkflowEventType
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {getEventHistoryIcon(eventType, className)}
      </TooltipTrigger>
      <TooltipContent side="top" className="flex items-center gap-4  shadow-lg">
        <span>{undoSlugify(eventType.toLowerCase())}</span>
      </TooltipContent>
    </Tooltip>
  )
}

function getEventHistoryIcon(eventType: WorkflowEventType, className?: string) {
  switch (eventType) {
    /* === Workflow Execution Events === */
    case "WORKFLOW_EXECUTION_STARTED":
      return <WorkflowIcon className={cn("fill-white stroke-sky-500/80", className)} />
    case "WORKFLOW_EXECUTION_COMPLETED":
      return (
        <WorkflowIcon
          className={cn("fill-white stroke-emerald-500", className)}
        />
      )
    case "WORKFLOW_EXECUTION_FAILED":
      return <CircleXIcon className={cn("fill-rose-500 stroke-white", className)} />
    case "WORKFLOW_EXECUTION_CANCELED":
      return (
        <CircleMinusIcon
          className={cn("fill-orange-500 stroke-white", className)}
        />
      )
    case "WORKFLOW_EXECUTION_TERMINATED":
      return (
        <CircleMinusIcon
          className={cn("fill-rose-500 stroke-white", className)}
        />
      )
    case "WORKFLOW_EXECUTION_CONTINUED_AS_NEW":
      return (
        <CircleArrowRightIcon
          className={cn("fill-sky-500/80 stroke-white", className)}
        />
      )
    case "WORKFLOW_EXECUTION_TIMED_OUT":
      return (
        <AlarmClockOffIcon
          className={cn("stroke-rose-500", className)}
          strokeWidth={2.5}
        />
      )
    /* === Child Workflow Execution Events === */
    case "START_CHILD_WORKFLOW_EXECUTION_INITIATED":
      return (
        <WorkflowIcon
          className={cn("fill-orange-200/50 stroke-orange-500/70", className)}
        />
      )
    case "CHILD_WORKFLOW_EXECUTION_STARTED":
      return (
        <WorkflowIcon
          className={cn("fill-orange-200/50 stroke-orange-500/70", className)}
        />
      )
    case "CHILD_WORKFLOW_EXECUTION_COMPLETED":
      return (
        <CircleCheck
          className={cn("fill-emerald-500 stroke-white", className)}
        />
      )
    case "CHILD_WORKFLOW_EXECUTION_FAILED":
      return <CircleXIcon className={cn("fill-rose-500 stroke-white", className)} />
    /* === Activity Task Events === */
    case "ACTIVITY_TASK_SCHEDULED":
      return (
        <CalendarCheckIcon
          className={cn("fill-orange-200/50 stroke-orange-500/70", className)}
        />
      )
    case "ACTIVITY_TASK_STARTED":
      return (
        <PlayIcon className={cn("fill-white stroke-sky-500/80", className)} />
      )
    case "ACTIVITY_TASK_COMPLETED":
      return (
        <CircleCheck
          className={cn("fill-emerald-500 stroke-white", className)}
        />
      )
    case "ACTIVITY_TASK_FAILED":
      return <CircleXIcon className={cn("fill-rose-500 stroke-white", className)} />
    case "ACTIVITY_TASK_TIMED_OUT":
      return (
        <AlarmClockOffIcon
          className={cn("stroke-orange-500", className)}
          strokeWidth={2.5}
        />
      )
    case "WORKFLOW_EXECUTION_UPDATE_ACCEPTED":
      return (
        <BookCheckIcon
          className={cn("fill-indigo-500/20 stroke-indigo-500/50", className)}
        />
      )
    case "WORKFLOW_EXECUTION_UPDATE_REJECTED":
      return (
        <CircleXIcon className={cn("fill-indigo-500/50 stroke-white", className)} />
      )
    case "WORKFLOW_EXECUTION_UPDATE_COMPLETED":
      return (
        <CircleCheck
          className={cn("fill-emerald-500/50 stroke-white", className)}
        />
      )

    default:
      return (
        <CircleDotIcon
          className={cn("fill-indigo-500/50 stroke-white", className)}
        />
      )
  }
}
