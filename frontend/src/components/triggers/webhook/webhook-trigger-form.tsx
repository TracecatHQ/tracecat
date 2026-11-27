"use client"

import { type FormEvent, useRef, useState } from "react"
import { triggersGetWebhook, type WebhookMethod } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import type { Tag } from "@/components/tags-input"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import type { TriggerKindFormProps } from "@/components/triggers/trigger-kind-types"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  normalizeCidrTags,
  WebhookAllowlistHint,
  WebhookAllowlistInput,
  WebhookIncludeHeadersToggle,
  WebhookMethodsSelect,
} from "@/components/webhooks/webhook-form-fields"
import { useWorkflowManager } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Settings for a new webhook trigger: the workflow whose webhook it is, then
 * the same webhook settings as the workflow page's Webhook panel. Every
 * workflow already has a webhook and its URL; this makes it a trigger.
 */
export function WebhookTriggerForm({
  formId,
  pending,
  onSubmit,
}: TriggerKindFormProps) {
  const workspaceId = useWorkspaceId()
  const canReadWorkflows = useScopeCheck("workflow:read")
  const { workflows, workflowsLoading } = useWorkflowManager(undefined, {
    listEnabled: canReadWorkflows === true,
  })
  const [workflowId, setWorkflowId] = useState("")
  const [methods, setMethods] = useState<WebhookMethod[]>(["POST"])
  const [cidrs, setCidrs] = useState<Tag[]>([])
  const [includeHeaders, setIncludeHeaders] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [loadingWebhook, setLoadingWebhook] = useState(false)
  // The workflow whose saved settings the fields hold. A create replaces
  // every setting, so it waits until they are this workflow's.
  const [loadedFor, setLoadedFor] = useState("")
  // Counts settings reads. Only the latest pick's read may fill the fields:
  // an earlier read of the same workflow can land after the user edited them.
  const readRef = useRef(0)

  const listed = workflows ?? []
  let placeholder = "Select a workflow"
  if (workflowsLoading) {
    placeholder = "Loading workflows…"
  } else if (canReadWorkflows !== true) {
    placeholder = "You can't list workflows"
  } else if (listed.length === 0) {
    placeholder = "No workflows yet"
  }

  // Start from the webhook's saved settings: a create replaces them all, and
  // an offline webhook may already have an allowlist.
  async function handleWorkflowChange(id: string) {
    setWorkflowId(id)
    readRef.current += 1
    const read = readRef.current
    setLoadedFor("")
    setFormError(null)
    setLoadingWebhook(true)
    try {
      const webhook = await triggersGetWebhook({ workspaceId, workflowId: id })
      if (readRef.current !== read) return
      setMethods(webhook.methods?.length ? webhook.methods : ["POST"])
      setCidrs(
        (webhook.allowlisted_cidrs ?? []).map((cidr) => ({
          id: cidr,
          text: cidr,
        }))
      )
      setIncludeHeaders(webhook.include_headers ?? false)
      setLoadedFor(id)
    } catch (error) {
      if (readRef.current === read) {
        // Clear the pick: choosing the same workflow again is the retry,
        // and a select reports no change for its current value.
        setWorkflowId("")
        setFormError(
          `Couldn't load this workflow's webhook settings. Pick it again to retry. ${describeTriggerError(error)}`
        )
      }
    } finally {
      if (readRef.current === read) {
        setLoadingWebhook(false)
      }
    }
  }

  function handleCidrsChange(tags: Tag[]) {
    const result = normalizeCidrTags(tags)
    if ("error" in result) {
      setFormError(result.error)
      return
    }
    setFormError(null)
    setCidrs(result.tags)
  }

  const locked = pending || loadingWebhook

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (loadingWebhook) return
    setFormError(null)
    if (!workflowId) {
      setFormError("Pick a workflow.")
      return
    }
    if (loadedFor !== workflowId) {
      setFormError(
        "Couldn't load this workflow's webhook settings. Pick the workflow again to retry."
      )
      return
    }
    if (methods.length === 0) {
      setFormError("Allow at least one HTTP method.")
      return
    }
    try {
      await onSubmit(async () => ({
        kind: "webhook",
        workflow_id: workflowId,
        methods,
        allowlisted_cidrs: cidrs.map((tag) => tag.text),
        include_headers: includeHeaders,
      }))
    } catch (error) {
      setFormError(describeTriggerError(error))
    }
  }

  return (
    <form id={formId} className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label className="text-xs">Workflow</Label>
        <Select
          value={workflowId}
          onValueChange={handleWorkflowChange}
          disabled={pending || workflowsLoading}
        >
          <SelectTrigger className="text-xs" aria-label="Workflow">
            <SelectValue placeholder={placeholder} />
          </SelectTrigger>
          <SelectContent>
            {listed.map((workflow) => (
              <SelectItem
                key={workflow.id}
                value={workflow.id}
                className="text-xs"
              >
                {workflow.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Uses this workflow&apos;s webhook and its URL, which don&apos;t
          change. It also shows on the workflow&apos;s Webhook panel.
        </p>
      </div>
      <fieldset disabled={locked} className="space-y-4">
        <WebhookIncludeHeadersToggle
          checked={includeHeaders}
          onCheckedChange={setIncludeHeaders}
          disabled={locked}
        />
        <div className="space-y-2">
          <Label className="text-xs">Allowed HTTP methods</Label>
          <WebhookMethodsSelect
            value={methods}
            onChange={setMethods}
            disabled={locked}
          />
        </div>
        <div className="space-y-2">
          <Label className="text-xs">IP allowlist</Label>
          <WebhookAllowlistInput
            tags={cidrs}
            onTagsChange={handleCidrsChange}
            disabled={locked}
          />
          <p className="text-xs text-muted-foreground">
            <WebhookAllowlistHint />
          </p>
        </div>
      </fieldset>
      {formError ? (
        <p className="whitespace-pre-line text-xs text-destructive">
          {formError}
        </p>
      ) : null}
    </form>
  )
}
