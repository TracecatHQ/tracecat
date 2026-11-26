"use client"

import { EventSourcePanel } from "@/components/triggers/object-storage/event-source-panel"
import type { TriggerKindDetailsProps } from "@/components/triggers/trigger-kind-types"

/** The settings and event source of an object storage trigger. */
export function ObjectStorageTriggerDetails({
  workspaceId,
  trigger,
}: TriggerKindDetailsProps) {
  const keyPrefix = trigger.config.key_prefix ?? ""
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-[120px_1fr] gap-x-4 gap-y-2 text-xs">
        <dt className="text-muted-foreground">Event source</dt>
        <dd>{trigger.config.source_name}</dd>
        <dt className="text-muted-foreground">Key prefix</dt>
        <dd className="font-mono">
          {keyPrefix || (
            <span className="font-sans text-muted-foreground">
              Every object
            </span>
          )}
        </dd>
      </dl>
      <EventSourcePanel
        workspaceId={workspaceId}
        sourceId={trigger.config.source_id}
        keyPrefix={keyPrefix}
      />
    </div>
  )
}
