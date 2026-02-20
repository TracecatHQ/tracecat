"use client"

import type { WorkflowExecutionEventStatus } from "@/client"
import {
  getAggregateWorkflowEventStatus,
  getWorkflowEventIcon,
} from "@/components/events/workflow-event-status"
import {
  WorkflowEventsList,
  type WorkflowEventsListRow,
} from "@/components/events/workflow-events-list"
import { CenteredSpinner, Spinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"
import {
  groupEventsByActionRef,
  executionId as parseWorkflowExecutionId,
  refToLabel,
  type WorkflowExecutionEventCompact,
} from "@/lib/event-history"
import { useCompactWorkflowExecution } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

import "react18-json-view/src/style.css"

const INTERMEDIATE_EVENT_STATUSES = new Set<WorkflowExecutionEventStatus>([
  "SCHEDULED",
  "STARTED",
])

/**
 * Events for a specific workflow execution
 */
export function WorkflowExecutionEventHistory({
  executionId,
  selectedEvent,
  setSelectedEvent,
}: {
  executionId: string
  selectedEvent?: WorkflowExecutionEventCompact
  setSelectedEvent: (event: WorkflowExecutionEventCompact) => void
}) {
  const workspaceId = useWorkspaceId()
  const decodedExecutionId = decodeURIComponent(executionId)
  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(decodedExecutionId)

  if (executionIsLoading) {
    return <CenteredSpinner />
  }
  if (executionError) {
    return <AlertNotification message={executionError.message} />
  }
  if (!execution) {
    return <NoContent message="No events found." />
  }

  const terminalEvents = execution.events.filter(
    (event) => !INTERMEDIATE_EVENT_STATUSES.has(event.status)
  )

  const eventRows = Object.entries(groupEventsByActionRef(terminalEvents))
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
      return getEventTimestamp(a.latestEvent) - getEventTimestamp(b.latestEvent)
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
    }) => ({
      key: actionRef,
      label: refToLabel(actionRef),
      time: latestEventTime,
      icon: getWorkflowEventIcon(aggregateStatus),
      selected: selectedEvent?.action_ref === actionRef,
      count: relatedEvents.length,
      subflowLink: childWorkflowRunLink,
      onSelect: latestEvent ? () => setSelectedEvent(latestEvent) : undefined,
    })
  )

  return (
    <div className="group h-full">
      <WorkflowEventsList rows={rows} />
    </div>
  )
}

function getEventTimestamp(event: WorkflowExecutionEventCompact): number {
  const eventTime = event.close_time || event.start_time || event.schedule_time
  return new Date(eventTime).getTime()
}

function getLatestEvent(
  relatedEvents: WorkflowExecutionEventCompact[]
): WorkflowExecutionEventCompact | undefined {
  return [...relatedEvents].sort(
    (a, b) => getEventTimestamp(b) - getEventTimestamp(a)
  )[0]
}

function getChildWorkflowRunLink(
  relatedEvents: WorkflowExecutionEventCompact[],
  workspaceId: string
): string | undefined {
  const eventWithChildRun = [...relatedEvents]
    .filter((event) => Boolean(event.child_wf_exec_id))
    .sort((a, b) => getEventTimestamp(b) - getEventTimestamp(a))[0]

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
