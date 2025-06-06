"use client"

import { useMemo } from "react"
import Link from "next/link"
import { CaseEventRead } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { AlertCircle, Clock, ExternalLinkIcon, PlusIcon } from "lucide-react"

import { SYSTEM_USER, User } from "@/lib/auth"
import { executionId, getWorkflowExecutionUrl } from "@/lib/event-history"
import { useAppInfo, useCaseEvents } from "@/lib/hooks"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  AssigneeChangedEvent,
  CaseClosedEvent,
  CaseReopenedEvent,
  CaseUpdatedEvent,
  EventActor,
  EventIcon,
  FieldsChangedEvent,
  PriorityChangedEvent,
  SeverityChangedEvent,
  StatusChangedEvent,
} from "@/components/cases/case-activity-feed-event"
import { CaseEventTimestamp } from "@/components/cases/case-panel-common"

import { InlineDotSeparator } from "../separator"

function ActivityFeedEvent({
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

        {/* Case updated events */}
        {event.type === "case_updated" && (
          <CaseUpdatedEvent event={event} actor={actor} />
        )}

        {/* Assignee events */}
        {event.type === "assignee_changed" && (
          <AssigneeChangedEvent event={event} actor={actor} userMap={users} />
        )}

        {/* Add a dot separator */}
        <InlineDotSeparator />
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <CaseEventTimestamp createdAt={event.created_at} showIcon={false} />
          {event.wf_exec_id && (
            <WorkflowExecutionInfo wfExecId={event.wf_exec_id} />
          )}
        </div>
      </div>
    </div>
  )
}

function WorkflowExecutionInfo({ wfExecId }: { wfExecId: string }) {
  const { workspaceId } = useWorkspace()
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

// Group activities by date for better organization
function groupActivitiesByDate(activities: CaseEventRead[]) {
  if (!activities || activities.length === 0) return []

  const grouped = activities.reduce(
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
    .map(([date, activities]) => ({
      date: new Date(date),
      activities: activities.sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      ),
    }))
}

export function CaseActivityFeed({
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
      <div className="space-y-4">
        <Skeleton className="h-8 w-40" />
        <div className="space-y-4">
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
      <div className="flex flex-col items-center justify-center p-6 text-center">
        <AlertCircle className="size-8 text-destructive" />
        <h3 className="mt-2 text-sm font-medium">Error loading activities</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {caseEventsError.message ||
            "An error occurred while loading activities."}
        </p>
      </div>
    )
  }

  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-6 text-center">
        <div className="rounded-full bg-muted p-3">
          <Clock className="size-6 text-muted-foreground" />
        </div>
        <h3 className="mt-2 text-sm font-medium">No activity yet</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Activities will appear here when changes are made to the case.
        </p>
      </div>
    )
  }

  const groupedActivities = groupActivitiesByDate(events)

  return (
    <div className="mx-auto w-full max-w-3xl">
      <div className="space-y-6">
        {groupedActivities.map(({ date, activities }) => (
          <div key={date.toISOString()} className="space-y-2">
            <div className="sticky top-0 z-10 bg-background py-2">
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
                {activities.map((event, index) => (
                  <ActivityFeedEvent key={index} event={event} users={users} />
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
