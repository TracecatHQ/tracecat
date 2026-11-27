"use client"

import { MousePointerClickIcon, WorkflowIcon, ZapIcon } from "lucide-react"
import Link from "next/link"
import type { TriggerRead, TriggerRunRead } from "@/client"
import { getExecutionStatusIcon } from "@/components/executions/nav"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import { Button } from "@/components/ui/button"
import {
  type TriggerPagedList,
  type TriggerRunTarget,
  useTriggerRunOrigin,
  useTriggerRuns,
  useTriggerStoppedEvents,
} from "@/hooks/use-trigger-runs"
import { getErrorStatus } from "@/lib/errors"
import { executionId as parseExecutionId } from "@/lib/event-history"
import { formatRelative } from "@/lib/time"
import { cn } from "@/lib/utils"

/** Where a run opens: the workflow run page or the agent's session. */
export function triggerRunHref(
  workspaceId: string,
  run: TriggerRunRead
): string | null {
  if (run.target_type === "agent") {
    if (!run.agent_preset_id || !run.session_id) {
      return null
    }
    return `/workspaces/${workspaceId}/agents/${run.agent_preset_id}?tab=live-chat&session=${run.session_id}`
  }
  const { wf, exec } = parseExecutionId(run.wf_exec_id)
  return `/workspaces/${workspaceId}/workflows/${wf}/executions/${exec}`
}

function runFallbackName(run: TriggerRunRead): string {
  return run.target_type === "agent"
    ? "Deleted agent session"
    : "Deleted workflow"
}

/** A trigger's recent runs and, for kinds that queue events, its stopped events. */
export function TriggerRunsSections({
  workspaceId,
  trigger,
}: {
  workspaceId: string
  trigger: TriggerRead
}) {
  return (
    <>
      <TriggerRecentRuns workspaceId={workspaceId} trigger={trigger} />
      {trigger.kind === "object_storage" ? (
        <TriggerStoppedEvents workspaceId={workspaceId} trigger={trigger} />
      ) : null}
    </>
  )
}

function TriggerRecentRuns({
  workspaceId,
  trigger,
}: {
  workspaceId: string
  trigger: TriggerRead
}) {
  const runs = useTriggerRuns(workspaceId, trigger.id)
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">Recent runs</h3>
        <p className="text-xs text-muted-foreground">
          Workflow runs and agent sessions this trigger started, running ones
          first.
        </p>
      </div>
      <PagedList
        list={runs}
        empty="No runs yet. Runs appear here once the trigger starts one."
        forbidden="You need permission to view workflow runs or agents to see this trigger's runs."
        renderItem={(run) => (
          <RunRow key={run.wf_exec_id} workspaceId={workspaceId} run={run} />
        )}
      />
    </section>
  )
}

function RunRow({
  workspaceId,
  run,
}: {
  workspaceId: string
  run: TriggerRunRead
}) {
  const isAgent = run.target_type === "agent"
  const Icon = isAgent ? MousePointerClickIcon : WorkflowIcon
  const href = triggerRunHref(workspaceId, run)
  const name = run.target_name ?? runFallbackName(run)
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <span title={run.status.toLowerCase()}>
        {getExecutionStatusIcon(run.status, "size-4 shrink-0")}
      </span>
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        {href ? (
          <Link
            href={href}
            className="block truncate text-xs font-medium hover:underline"
          >
            {name}
          </Link>
        ) : (
          <span className="block truncate text-xs font-medium">{name}</span>
        )}
      </div>
      <span className="w-14 shrink-0 text-xs text-muted-foreground">
        {isAgent ? "Agent" : "Workflow"}
      </span>
      <span
        className="w-28 shrink-0 text-right text-xs text-muted-foreground"
        title={new Date(run.start_time).toLocaleString()}
      >
        {formatRelative(run.start_time)}
      </span>
    </div>
  )
}

function TriggerStoppedEvents({
  workspaceId,
  trigger,
}: {
  workspaceId: string
  trigger: TriggerRead
}) {
  const events = useTriggerStoppedEvents(workspaceId, trigger.id)
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">Stopped events</h3>
        <p className="text-xs text-muted-foreground">
          Events whose targets failed to start 8 times in a row. They are no
          longer retried.
        </p>
      </div>
      <PagedList
        list={events}
        empty="No stopped events."
        forbidden="You don't have permission to view this trigger's events."
        renderItem={(event) => (
          <div key={event.id} className="space-y-1 px-4 py-2.5">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="font-medium">Event {event.id}</span>
              <span
                className="text-muted-foreground"
                title={new Date(event.created_at).toLocaleString()}
              >
                Received {formatRelative(event.created_at)} · {event.attempts}{" "}
                attempts
              </span>
            </div>
            {event.error ? (
              <p className="break-words font-mono text-xs text-muted-foreground">
                {event.error}
              </p>
            ) : null}
          </div>
        )}
      />
    </section>
  )
}

function PagedList<T>({
  list,
  empty,
  forbidden,
  renderItem,
}: {
  list: TriggerPagedList<T>
  empty: string
  forbidden: string
  renderItem: (item: T) => React.ReactNode
}) {
  if (list.isLoading) {
    return <p className="text-xs text-muted-foreground">Loading...</p>
  }
  if (list.error) {
    return (
      <p className="text-xs text-destructive">
        {getErrorStatus(list.error) === 403
          ? forbidden
          : `Couldn't load this list. ${describeTriggerError(list.error)}`}
      </p>
    )
  }
  if (list.items.length === 0) {
    return (
      <p className="rounded-md border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
        {empty}
      </p>
    )
  }
  return (
    <div className="space-y-2">
      <div className="divide-y rounded-md border">
        {list.items.map(renderItem)}
      </div>
      {list.hasNextPage ? (
        <Button
          variant="outline"
          size="sm"
          className="h-7"
          disabled={list.isFetchingNextPage}
          onClick={list.fetchNextPage}
        >
          Load more
        </Button>
      ) : null}
    </div>
  )
}

/**
 * "Started by <trigger>" for a workflow run or an agent session, linking to
 * the trigger. Renders nothing when no trigger started it or it can't be read.
 */
export function StartedByTrigger({
  workspaceId,
  target,
  className,
}: {
  workspaceId: string
  target: TriggerRunTarget
  className?: string
}) {
  const { data: origin } = useTriggerRunOrigin(workspaceId, target)
  if (!origin) {
    return null
  }
  const label = origin.name ?? "a deleted trigger"
  return (
    <span
      className={cn(
        "inline-flex min-w-0 items-center gap-1 text-xs text-muted-foreground",
        className
      )}
    >
      <ZapIcon className="size-3 shrink-0" />
      <span className="shrink-0">Started by</span>
      {origin.name ? (
        <Link
          href={`/workspaces/${workspaceId}/triggers/${origin.trigger_id}`}
          className="truncate font-medium text-foreground hover:underline"
        >
          {label}
        </Link>
      ) : (
        <span className="truncate">{label}</span>
      )}
    </span>
  )
}
