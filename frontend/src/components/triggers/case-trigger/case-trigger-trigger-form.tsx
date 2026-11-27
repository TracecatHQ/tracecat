"use client"

import { type FormEvent, useRef, useState } from "react"
import { type CaseEventType, triggersGetCaseTrigger } from "@/client"
import {
  CaseEventsField,
  CaseTagFiltersField,
} from "@/components/case-triggers/case-trigger-form-fields"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import {
  TriggerFormError,
  TriggerWorkflowSelect,
} from "@/components/triggers/trigger-form-fields"
import type { TriggerKindFormProps } from "@/components/triggers/trigger-kind-types"
import { Label } from "@/components/ui/label"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Settings for a new case trigger trigger: the workflow whose case trigger it
 * is, then the same case events and tag allowlist as the workflow page's Case
 * triggers panel. Every workflow already has a case trigger; this makes it a
 * trigger.
 */
export function CaseTriggerTriggerForm({
  formId,
  pending,
  onSubmit,
}: TriggerKindFormProps) {
  const workspaceId = useWorkspaceId()
  const [workflowId, setWorkflowId] = useState("")
  const [eventTypes, setEventTypes] = useState<CaseEventType[]>([])
  const [tagFilters, setTagFilters] = useState<string[]>([])
  const [formError, setFormError] = useState<string | null>(null)
  const [loadingSettings, setLoadingSettings] = useState(false)
  // The workflow whose saved settings the fields hold. A create sends every
  // setting, so it waits until they are this workflow's.
  const [loadedFor, setLoadedFor] = useState("")
  // The picked case trigger's on/off status. Creating keeps it: a live case
  // trigger stays live.
  const [loadedStatus, setLoadedStatus] = useState<"online" | "offline" | null>(
    null
  )
  // Counts settings reads. Only the latest pick's read may fill the fields:
  // an earlier read of the same workflow can land after the user edited them.
  const readRef = useRef(0)

  // Start from the case trigger's saved settings: an offline case trigger may
  // already have events and tags.
  async function handleWorkflowChange(id: string) {
    setWorkflowId(id)
    readRef.current += 1
    const read = readRef.current
    setLoadedFor("")
    setLoadedStatus(null)
    setFormError(null)
    setLoadingSettings(true)
    try {
      const caseTrigger = await triggersGetCaseTrigger({
        workspaceId,
        workflowId: id,
      })
      if (readRef.current !== read) return
      setEventTypes(caseTrigger.event_types ?? [])
      setTagFilters(caseTrigger.tag_filters ?? [])
      setLoadedStatus(caseTrigger.status)
      setLoadedFor(id)
    } catch (error) {
      if (readRef.current === read) {
        // Clear the pick: choosing the same workflow again is the retry,
        // and a select reports no change for its current value.
        setWorkflowId("")
        setFormError(
          `Couldn't load this workflow's case trigger settings. Pick it again to retry. ${describeTriggerError(error)}`
        )
      }
    } finally {
      if (readRef.current === read) {
        setLoadingSettings(false)
      }
    }
  }

  const locked = pending || loadingSettings

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (loadingSettings) {
      setFormError(
        "Loading this workflow's case trigger settings. Try again in a moment."
      )
      return
    }
    setFormError(null)
    if (!workflowId) {
      setFormError("Pick a workflow.")
      return
    }
    if (loadedFor !== workflowId) {
      setFormError(
        "Couldn't load this workflow's case trigger settings. Pick the workflow again to retry."
      )
      return
    }
    if (loadedStatus === "online" && eventTypes.length === 0) {
      setFormError(
        "This case trigger is online, so it needs at least one case event."
      )
      return
    }
    try {
      await onSubmit(async () => ({
        kind: "case_trigger",
        workflow_id: workflowId,
        event_types: eventTypes,
        tag_filters: tagFilters,
      }))
    } catch (error) {
      setFormError(describeTriggerError(error))
    }
  }

  return (
    <form id={formId} className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label className="text-xs">Workflow</Label>
        <TriggerWorkflowSelect
          kind="case_trigger"
          value={workflowId}
          onValueChange={handleWorkflowChange}
          disabled={pending}
        />
        <p className="text-xs text-muted-foreground">
          Uses this workflow&apos;s case trigger. It also shows on the
          workflow&apos;s Case triggers panel.
        </p>
        {loadedStatus === "online" ? (
          <p className="text-xs text-muted-foreground">
            This case trigger is online and stays online.
          </p>
        ) : null}
      </div>
      <fieldset disabled={locked} className="space-y-4">
        <CaseEventsField
          value={eventTypes}
          onChange={setEventTypes}
          disabled={locked}
        />
        <CaseTagFiltersField
          value={tagFilters}
          onChange={setTagFilters}
          disabled={locked}
        />
      </fieldset>
      <TriggerFormError message={formError} />
    </form>
  )
}
