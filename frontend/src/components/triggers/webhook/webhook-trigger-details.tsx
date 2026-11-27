"use client"

import Link from "next/link"
import type { TriggerRead } from "@/client"
import { CopyableValue } from "@/components/triggers/object-storage/copyable-value"
import type { TriggerKindDetailsProps } from "@/components/triggers/trigger-kind-types"
import { workflowUuidToShortId } from "@/lib/workflow-ids"

/** "POST" or "GET, POST · 2 allowed IPs", for the list's source column. */
export function describeWebhook(trigger: TriggerRead): string {
  if (trigger.config.kind !== "webhook") {
    return ""
  }
  const methods = [...trigger.config.methods].sort().join(", ")
  const allowed = trigger.config.allowlisted_cidrs.length
  if (allowed === 0) {
    return methods
  }
  return `${methods} · ${allowed} allowed ${allowed === 1 ? "IP range" : "IP ranges"}`
}

function apiKeyState(trigger: TriggerRead): string {
  if (trigger.config.kind !== "webhook" || !trigger.config.api_key) {
    return "None"
  }
  const { api_key: key } = trigger.config
  return key.is_active
    ? `Active (${key.preview})`
    : `Revoked (${key.preview}): requests are refused`
}

/**
 * The settings of a webhook trigger: its workflow, URL and request rules.
 * Changes go through the workflow's Webhook panel, which edits the same row.
 */
export function WebhookTriggerDetails({
  workspaceId,
  trigger,
}: TriggerKindDetailsProps) {
  if (trigger.config.kind !== "webhook") {
    return null
  }
  const config = trigger.config
  const [binding] = trigger.bindings
  const muted = <span className="text-muted-foreground">None</span>
  const workflowHref = `/workspaces/${workspaceId}/workflows/${workflowUuidToShortId(config.workflow_id)}`
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">URL</p>
        <CopyableValue value={config.url} toastMessage="Copied URL" />
        <p className="text-xs text-muted-foreground">
          The URL contains the webhook&apos;s secret. Share it only with the
          sender.
        </p>
      </div>
      <dl className="grid grid-cols-[120px_1fr] gap-x-4 gap-y-2 text-xs">
        <dt className="text-muted-foreground">Workflow</dt>
        <dd>
          <Link href={workflowHref} className="hover:underline">
            {binding?.target_name ?? config.workflow_id}
          </Link>
        </dd>
        <dt className="text-muted-foreground">Methods</dt>
        <dd>{[...config.methods].sort().join(", ") || muted}</dd>
        <dt className="text-muted-foreground">IP allowlist</dt>
        <dd className="font-mono">
          {config.allowlisted_cidrs.length
            ? config.allowlisted_cidrs.join(", ")
            : muted}
        </dd>
        <dt className="text-muted-foreground">Request headers</dt>
        <dd>{config.include_headers ? "Included" : "Body only"}</dd>
        <dt className="text-muted-foreground">API key</dt>
        <dd>{apiKeyState(trigger)}</dd>
        <dt className="text-muted-foreground">Webhook ID</dt>
        <dd className="font-mono">{config.webhook_id}</dd>
      </dl>
      <p className="text-xs text-muted-foreground">
        This is the workflow&apos;s webhook: turning it on or off here and on
        the workflow&apos;s Webhook panel is the same change. Edit its methods,
        allowlist and API key on the{" "}
        <Link href={workflowHref} className="underline">
          workflow page
        </Link>
        .
      </p>
    </div>
  )
}
