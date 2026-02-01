"use client"

import { AlertCircle, Clock, ExternalLinkIcon, PlusIcon } from "lucide-react"
import Link from "next/link"
import { useMemo } from "react"
import type { CaseEventRead } from "@/client"
import {
  AssigneeChangedEvent,
  AttachmentCreatedEvent,
  AttachmentDeletedEvent,
  CaseClosedEvent,
  CaseReopenedEvent,
  CaseUpdatedEvent,
  CaseViewedEvent,
  DropdownValueChangedEvent,
  EventActor,
  EventCreatedAt,
  EventIcon,
  FieldsChangedEvent,
  PayloadChangedEvent,
  PriorityChangedEvent,
  SeverityChangedEvent,
  StatusChangedEvent,
  TaskAssigneeChangedEvent,
  TaskCreatedEvent,
  TaskDeletedEvent,
  TaskPriorityChangedEvent,
  TaskStatusChangedEvent,
  TaskWorkflowChangedEvent,
} from "@/components/cases/cases-feed-event"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { SYSTEM_USER, User } from "@/lib/auth"
import { executionId, getWorkflowExecutionUrl } from "@/lib/event-history"
import { useAppInfo, useCaseEvents } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

import { InlineDotSeparator } from "../separator"

function CaseFeedEvent({
  event,
  users,
}: {
  event: CaseEventRead
  users: Record<string, User>
}) {
  const actor = event.user_id ? users[event.user_id] : SYSTEM_USER

  return (
    <div className="relative">
      <div className="flex items-center space-x-1">
        {/* Status events */}
        {event.type === "status_changed" && (
          <StatusChangedEvent event={event} actor={actor} />
        )}

        {/* Priority events */}
        {event.type === "priority_changed" && (
          <PriorityChangedEvent event={event} actor={actor} />
        )}

        {/* Severity events */}
        {event.type === "severity_changed" && (
          <SeverityChangedEvent event={event} actor={actor} />
        )}
        {/* Case field events */}
        {event.type === "fields_changed" && (
          <FieldsChangedEvent event={event} actor={actor} />
        )}

        {/* Case events */}
        {event.type === "case_created" && (
          <div className="flex items-center space-x-2 text-xs">
            <EventIcon icon={PlusIcon} />
            <span>
              <EventActor user={actor} /> created the case
            </span>
          </div>
        )}

        {event.type === "case_closed" && (
          <CaseClosedEvent event={event} actor={actor} />
        )}

        {event.type === "case_reopened" && (
          <CaseReopenedEvent event={event} actor={actor} />
        )}

        {event.type === "case_viewed" && (
          <CaseViewedEvent event={event} actor={actor} />
        )}

        {/* Case updated events */}
        {event.type === "case_updated" && (
          <CaseUpdatedEvent event={event} actor={actor} />
        )}

        {/* Assignee events */}
        {event.type === "assignee_changed" && (
          <AssigneeChangedEvent event={event} actor={actor} userMap={users} />
        )}

        {/* Task events */}
        {event.type === "task_created" && (
          <TaskCreatedEvent event={event} actor={actor} />
        )}
        {event.type === "task_deleted" && (
          <TaskDeletedEvent event={event} actor={actor} />
        )}
        {event.type === "task_status_changed" && (
          <TaskStatusChangedEvent event={event} actor={actor} />
        )}
        {event.type === "task_priority_changed" && (
          <TaskPriorityChangedEvent event={event} actor={actor} />
        )}
        {event.type === "task_workflow_changed" && (
          <TaskWorkflowChangedEvent event={event} actor={actor} />
        )}
        {event.type === "task_assignee_changed" && (
          <TaskAssigneeChangedEvent
            event={event}
            actor={actor}
            userMap={users}
          />
        )}

        {/* Attachment events */}
        {event.type === "attachment_created" && (
          <AttachmentCreatedEvent event={event} actor={actor} />
        )}

        {event.type === "attachment_deleted" && (
          <AttachmentDeletedEvent event={event} actor={actor} />
        )}

        {event.type === "payload_changed" && (
          <PayloadChangedEvent event={event} actor={actor} />
        )}

        {event.type === "dropdown_value_changed" && (
          <DropdownValueChangedEvent event={event} actor={actor} />
        )}
        {/* Add a dot separator */}
        <InlineDotSeparator />
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <EventCreatedAt createdAt={event.created_at} />
          {event.wf_exec_id && (
            <WorkflowExecutionInfo wfExecId={event.wf_exec_id} />
          )}
        </div>
      </div>
    </div>
  )
}

function WorkflowExecutionInfo({ wfExecId }: { wfExecId: string }) {
  const workspaceId = useWorkspaceId()
  const { appInfo } = useAppInfo()
  const baseUrl = appInfo?.public_app_url
  const { wf, exec } = executionId(wfExecId)
  const url = baseUrl
    ? getWorkflowExecutionUrl(baseUrl, workspaceId, wf, exec)
    : null
  return (
    <div className="text-xs text-muted-foreground">
      {url ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Link
                href={url}
                className="hover:text-foreground hover:underline"
                target="_blank"
              >
                via workflow
              </Link>
            </TooltipTrigger>
            <TooltipContent className="flex items-center gap-2">
              Open workflow execution <ExternalLinkIcon className="size-3" />
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        <span>via workflow</span>
      )}
    </div>
  )
}

// Group events by date for better organization
function groupEventsByDate(events: CaseEventRead[]) {
  if (!events || events.length === 0) return []

  const grouped = events.reduce(
    (grouped: Record<string, CaseEventRead[]>, event) => {
      const date = new Date(event.created_at).toDateString()
      if (!grouped[date]) grouped[date] = []
      grouped[date].push(event)
      return grouped
    },
    {}
  )

  // Sort dates in ascending order (oldest first)
  return Object.entries(grouped)
    .sort(
      ([dateA], [dateB]) =>
        new Date(dateA).getTime() - new Date(dateB).getTime()
    )
    .map(([date, events]) => ({
      date: new Date(date),
      events: events.sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      ),
    }))
}

export function CaseFeed({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { caseEvents, caseEventsIsLoading, caseEventsError } = useCaseEvents({
    caseId,
    workspaceId,
  })
  const events = caseEvents?.events ?? []
  const users = useMemo(() => {
    if (!caseEvents || !Array.isArray(caseEvents.users)) {
      return {}
    }
    return caseEvents.users.reduce((acc: Record<string, User>, userRead) => {
      acc[userRead.id] = new User(userRead)
      return acc
    }, {})
  }, [caseEvents])

  if (caseEventsIsLoading) {
    return (
      <div className="mx-auto w-full">
        <div className="space-y-4 p-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (caseEventsError) {
    return (
      <div className="mx-auto w-full">
        <div className="space-y-4 p-4">
          <div className="flex items-center justify-center p-8">
            <div className="flex items-center gap-2 text-red-600">
              <AlertCircle className="h-4 w-4" />
              <span className="text-sm">Failed to load events</span>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (events.length === 0) {
    return (
      <div className="mx-auto w-full">
        <div className="p-4">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Clock className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No events yet</EmptyTitle>
              <EmptyDescription>
                Events will appear here when changes are made to the case.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      </div>
    )
  }

  const groupedEvents = groupEventsByDate(events)

  return (
    <div className="mx-auto w-full">
      <div className="space-y-4 p-4">
        {groupedEvents.map(({ date, events: dateEvents }) => (
          <div key={date.toISOString()} className="space-y-2">
            <div className="sticky top-0 z-10 py-2">
              <div className="flex items-center">
                <div className="text-sm font-medium">
                  {date.toLocaleDateString(undefined, {
                    weekday: "long",
                    month: "long",
                    day: "numeric",
                    year: "numeric",
                  })}
                </div>
                {/* <Separator className="ml-4 flex-1" /> */}
              </div>
            </div>

            <div className="relative">
              <div
                className="absolute inset-y-0 left-2 w-px bg-gray-200"
                aria-hidden="true"
              />
              <div className="space-y-2">
                {dateEvents.map((event, index) => (
                  <CaseFeedEvent key={index} event={event} users={users} />
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
