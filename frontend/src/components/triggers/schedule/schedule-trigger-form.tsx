"use client"

import { useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  ScheduleFormFields,
  type ScheduleInputs,
  scheduleInputsToSettings,
  useScheduleForm,
} from "@/components/schedules/schedule-form-fields"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import type { TriggerKindFormProps } from "@/components/triggers/trigger-kind-types"
import { Form } from "@/components/ui/form"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useWorkflowManager } from "@/lib/hooks"

/**
 * Settings for a new schedule trigger: the workflow it runs, then the same
 * schedule fields as the workflow page's "Create schedule" dialog.
 */
export function ScheduleTriggerForm({
  formId,
  pending,
  onSubmit,
}: TriggerKindFormProps) {
  const canReadWorkflows = useScopeCheck("workflow:read")
  const { workflows, workflowsLoading } = useWorkflowManager(undefined, {
    listEnabled: canReadWorkflows === true,
  })
  const form = useScheduleForm()
  const [workflowId, setWorkflowId] = useState("")
  const [formError, setFormError] = useState<string | null>(null)

  // A schedule runs the saved definition, so a never-saved workflow can't
  // have one.
  const saved = (workflows ?? []).filter((workflow) => workflow.version)
  let placeholder = "Select a workflow"
  if (workflowsLoading) {
    placeholder = "Loading workflows…"
  } else if (canReadWorkflows !== true) {
    placeholder = "You can't list workflows"
  } else if (saved.length === 0) {
    placeholder = "No saved workflows yet"
  }

  async function handleSubmit(values: ScheduleInputs) {
    setFormError(null)
    if (!workflowId) {
      setFormError("Pick a workflow.")
      return
    }
    try {
      await onSubmit(async () => ({
        kind: "schedule",
        workflow_id: workflowId,
        ...scheduleInputsToSettings(values),
      }))
    } catch (error) {
      setFormError(describeTriggerError(error))
    }
  }

  return (
    <Form {...form}>
      <form
        id={formId}
        className="space-y-4"
        onSubmit={form.handleSubmit(handleSubmit)}
      >
        <div className="space-y-2">
          <Label className="text-xs">Workflow</Label>
          <Select
            value={workflowId}
            onValueChange={setWorkflowId}
            disabled={pending || workflowsLoading}
          >
            <SelectTrigger className="text-xs" aria-label="Workflow">
              <SelectValue placeholder={placeholder} />
            </SelectTrigger>
            <SelectContent>
              {saved.map((workflow) => (
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
            The schedule runs this workflow&apos;s saved version. It also shows
            on the workflow&apos;s Schedules panel.
          </p>
        </div>
        <fieldset disabled={pending} className="space-y-4">
          <ScheduleFormFields form={form} />
        </fieldset>
        {formError ? (
          <p className="whitespace-pre-line text-xs text-destructive">
            {formError}
          </p>
        ) : null}
      </form>
    </Form>
  )
}
