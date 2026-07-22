"use client"

import { Repeat2Icon } from "lucide-react"
import {
  getAggregateWorkflowEventStatus,
  getWorkflowEventIcon,
} from "@/components/events/workflow-event-status"
import {
  WorkflowEventsList,
  type WorkflowEventsListRow,
} from "@/components/events/workflow-events-list"
import { Spinner } from "@/components/loading/spinner"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  getCompactEventTimestamp,
  getLatestLoopEventMeta,
  getLoopEventMeta,
  groupEventsByActionRef,
  executionId as parseWorkflowExecutionId,
  refToLabel,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { useWorkspaceId } from "@/providers/workspace-id"

import "react18-json-view/src/style.css"

/** Events for a specific workflow execution. */
export function WorkflowExecutionEventHistory({
  execution,
  selectedActionRef,
  setSelectedActionRef,
}: {
  execution: WorkflowExecutionReadCompact
  selectedActionRef?: string
  setSelectedActionRef: (actionRef: string) => void
}) {
  const workspaceId = useWorkspaceId()

  const eventRows = Object.entries(groupEventsByActionRef(execution.events))
    .map(([actionRef, relatedEvents]) => {
      const aggregateStatus = getAggregateWorkflowEventStatus(relatedEvents)
      const latestEvent = getLatestEvent(relatedEvents)
      const latestEventTime = latestEvent
        ? new Date(
            latestEvent.close_time ||
              latestEvent.start_time ||
              latestEvent.schedule_time
          ).toLocaleTimeString()
        : "-"
      const childWorkflowRunLink = getChildWorkflowRunLink(
        relatedEvents,
        workspaceId
      )

      return {
        actionRef,
        relatedEvents,
        aggregateStatus,
        latestEvent,
        latestEventTime,
        childWorkflowRunLink,
      }
    })
    .sort((a, b) => {
      if (!a.latestEvent || !b.latestEvent) {
        return 0
      }
      return (
        getCompactEventTimestamp(a.latestEvent) -
        getCompactEventTimestamp(b.latestEvent)
      )
    })

  if (eventRows.length === 0) {
    return (
      <div className="flex h-16 items-center justify-center text-center text-xs text-muted-foreground">
        {execution.status === "RUNNING" ? (
          <div className="flex items-center justify-center gap-2">
            <Spinner className="size-3" />
            <span>Waiting for events...</span>
          </div>
        ) : (
          <span>No events</span>
        )}
      </div>
    )
  }

  const rows: WorkflowEventsListRow[] = eventRows.map(
    ({
      actionRef,
      relatedEvents,
      aggregateStatus,
      latestEvent,
      latestEventTime,
      childWorkflowRunLink,
    }) => {
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

      return {
        key: actionRef,
        label: refToLabel(actionRef),
        meta: loopBadge,
        time: latestEventTime,
        icon: isLoopAction ? (
          loopTooltip ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="relative flex size-5 items-center justify-center">
                  {getWorkflowEventIcon(aggregateStatus)}
                  <Repeat2Icon className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-sm bg-orange-100 text-orange-700 ring-1 ring-orange-200" />
                </div>
              </TooltipTrigger>
              <TooltipContent side="top">
                <span>{loopTooltip}</span>
              </TooltipContent>
            </Tooltip>
          ) : (
            <div className="relative flex size-5 items-center justify-center">
              {getWorkflowEventIcon(aggregateStatus)}
              <Repeat2Icon className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-sm bg-orange-100 text-orange-700 ring-1 ring-orange-200" />
            </div>
          )
        ) : (
          getWorkflowEventIcon(aggregateStatus)
        ),
        selected: selectedActionRef === actionRef,
        count: relatedEvents.length,
        subflowLink: childWorkflowRunLink,
        onSelect: latestEvent
          ? () => setSelectedActionRef(actionRef)
          : undefined,
      }
    }
  )

  return (
    <div className="group h-full">
      <WorkflowEventsList rows={rows} />
    </div>
  )
}

function getLatestEvent(
  relatedEvents: WorkflowExecutionEventCompact[]
): WorkflowExecutionEventCompact | undefined {
  return [...relatedEvents].sort(
    (a, b) => getCompactEventTimestamp(b) - getCompactEventTimestamp(a)
  )[0]
}

function getChildWorkflowRunLink(
  relatedEvents: WorkflowExecutionEventCompact[],
  workspaceId: string
): string | undefined {
  const eventWithChildRun = [...relatedEvents]
    .filter((event) => Boolean(event.child_wf_exec_id))
    .sort(
      (a, b) => getCompactEventTimestamp(b) - getCompactEventTimestamp(a)
    )[0]

  if (!eventWithChildRun?.child_wf_exec_id) {
    return undefined
  }

  try {
    const childExecution = parseWorkflowExecutionId(
      eventWithChildRun.child_wf_exec_id
    )
    return `/workspaces/${workspaceId}/workflows/${childExecution.wf}/executions/${childExecution.exec}`
  } catch {
    return undefined
  }
}
