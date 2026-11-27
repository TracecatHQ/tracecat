"use client"

import Link from "next/link"
import type { TriggerRead } from "@/client"
import { CASE_EVENT_SUGGESTIONS } from "@/components/builder/panel/case-event-suggestions"
import type { TriggerKindDetailsProps } from "@/components/triggers/trigger-kind-types"
import { workflowUuidToShortId } from "@/lib/workflow-ids"

const EVENT_LABELS = new Map(
  CASE_EVENT_SUGGESTIONS.map((suggestion) => [
    suggestion.value,
    suggestion.label,
  ])
)

function eventLabel(eventType: string): string {
  return EVENT_LABELS.get(eventType) ?? eventType
}

/** "Case closed, Case created · 1 tag", for the list's source column. */
export function describeCaseTrigger(trigger: TriggerRead): string {
  if (trigger.config.kind !== "case_trigger") {
    return ""
  }
  const events = trigger.config.event_types.map(eventLabel).join(", ")
  const label = events || "No case events"
  const tags = trigger.config.tag_filters.length
  if (tags === 0) {
    return label
  }
  return `${label} · ${tags} ${tags === 1 ? "tag" : "tags"}`
}

/**
 * The settings of a case trigger trigger: its workflow, case events and tag
 * allowlist. The workflow page's Case triggers panel edits the same row.
 */
export function CaseTriggerTriggerDetails({
  workspaceId,
  trigger,
}: TriggerKindDetailsProps) {
  if (trigger.config.kind !== "case_trigger") {
    return null
  }
  const config = trigger.config
  const [binding] = trigger.bindings
  const muted = <span className="text-muted-foreground">None</span>
  const workflowHref = `/workspaces/${workspaceId}/workflows/${workflowUuidToShortId(config.workflow_id)}`
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[120px_1fr] gap-x-4 gap-y-2 text-xs">
        <dt className="text-muted-foreground">Workflow</dt>
        <dd>
          <Link href={workflowHref} className="hover:underline">
            {binding?.target_name ?? config.workflow_id}
          </Link>
        </dd>
        <dt className="text-muted-foreground">Case events</dt>
        <dd>{config.event_types.map(eventLabel).join(", ") || muted}</dd>
        <dt className="text-muted-foreground">Tag allowlist</dt>
        <dd className="font-mono">
          {config.tag_filters.length ? config.tag_filters.join(", ") : muted}
        </dd>
        <dt className="text-muted-foreground">Case trigger ID</dt>
        <dd className="font-mono">{config.case_trigger_id}</dd>
      </dl>
      <p className="text-xs text-muted-foreground">
        This is the workflow&apos;s case trigger: turning it on or off here and
        on the workflow&apos;s Case triggers panel is the same change. Edit its
        case events and tag allowlist on the{" "}
        <Link href={workflowHref} className="underline">
          workflow page
        </Link>
        .
      </p>
    </div>
  )
}
