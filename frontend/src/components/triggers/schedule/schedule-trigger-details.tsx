"use client"

import Link from "next/link"
import type { TriggerRead } from "@/client"
import type { TriggerKindDetailsProps } from "@/components/triggers/trigger-kind-types"
import { durationToHumanReadable } from "@/lib/time"
import { workflowUuidToShortId } from "@/lib/workflow-ids"

/**
 * "0 9 * * *" or "Every 1 hour", for the list and the detail page. A schedule
 * that `/schedules` left with both shows both: which one runs depends on which
 * was set last, which the row doesn't record.
 */
export function describeSchedule(trigger: TriggerRead): string {
  if (trigger.config.kind !== "schedule") {
    return ""
  }
  const { cron, every } = trigger.config
  const interval = every ? `Every ${humanDuration(every)}` : ""
  if (cron && interval) {
    return `${cron} or ${interval.toLowerCase()}`
  }
  return cron || interval
}

function humanDuration(duration: string): string {
  try {
    return durationToHumanReadable(duration)
  } catch {
    return duration
  }
}

function formatDate(value?: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/** The settings of a schedule trigger: its workflow and when it runs. */
export function ScheduleTriggerDetails({
  workspaceId,
  trigger,
}: TriggerKindDetailsProps) {
  if (trigger.config.kind !== "schedule") {
    return null
  }
  const config = trigger.config
  const [binding] = trigger.bindings
  const muted = <span className="text-muted-foreground">None</span>
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[120px_1fr] gap-x-4 gap-y-2 text-xs">
        <dt className="text-muted-foreground">Workflow</dt>
        <dd>
          {binding ? (
            <Link
              href={`/workspaces/${workspaceId}/workflows/${workflowUuidToShortId(binding.target_id)}`}
              className="hover:underline"
            >
              {binding.target_name}
            </Link>
          ) : (
            muted
          )}
        </dd>
        <dt className="text-muted-foreground">Runs</dt>
        <dd className={config.cron ? "font-mono" : undefined}>
          {describeSchedule(trigger) || muted}
        </dd>
        {config.offset ? (
          <>
            <dt className="text-muted-foreground">Offset</dt>
            <dd>{humanDuration(config.offset)}</dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Starts</dt>
        <dd>{formatDate(config.start_at) ?? muted}</dd>
        <dt className="text-muted-foreground">Ends</dt>
        <dd>{formatDate(config.end_at) ?? muted}</dd>
        <dt className="text-muted-foreground">Timeout</dt>
        <dd>{config.timeout ? `${config.timeout} s` : muted}</dd>
        <dt className="text-muted-foreground">Schedule ID</dt>
        <dd className="font-mono">{config.schedule_id}</dd>
      </dl>
      <p className="text-xs text-muted-foreground">
        This is the workflow&apos;s schedule: changes here and on the
        workflow&apos;s Schedules panel are the same change.
      </p>
    </div>
  )
}
