"use client"

import { ExternalLinkIcon } from "lucide-react"
import Link from "next/link"
import type { WorkflowRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CopyButton } from "@/components/copy-button"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import {
  useCreateForm,
  useFormDetail,
  useForms,
  useUpdateForm,
} from "@/hooks/use-forms"
import { getApiErrorDetail } from "@/lib/errors"
import { getFormErrorDetail } from "@/lib/forms/errors"
import { workflowShortIdToUuid } from "@/lib/forms/workflow-id"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * The Form tab of the workflow builder's trigger panel: a switch that puts
 * the workflow's form online, the link requesters open, and a link to the
 * form's page.
 *
 * A workflow has at most one form. Turning the switch on for a workflow
 * without one creates it. A form can only be online for a published
 * workflow, so the switch is disabled until the first publish. The form's
 * layout is edited on the Forms page, not here.
 */
export function FormTriggerControls({
  workflow,
}: {
  workflow: Pick<WorkflowRead, "id" | "version">
}) {
  const workspaceId = useWorkspaceId()
  const canCreate = useScopeCheck("form:create")
  const canUpdate = useScopeCheck("form:update")
  const { forms, formsIsLoading, formsError } = useForms(workspaceId)
  const workflowUuid = workflowShortIdToUuid(workflow.id) ?? workflow.id
  const listed = forms.find((form) => form.workflow_id === workflowUuid)
  // The list has no URL. The detail carries the one requesters open.
  const { form: detail } = useFormDetail(
    { workspaceId, formId: listed?.id },
    { enabled: Boolean(listed) }
  )
  const { createForm, createFormIsPending } = useCreateForm(workspaceId)
  const { updateForm, updateFormIsPending } = useUpdateForm({
    workspaceId,
    formId: listed?.id ?? "",
  })

  if (formsIsLoading) {
    return <CenteredSpinner />
  }
  if (formsError) {
    return <p className="text-xs text-destructive">Couldn't load the form.</p>
  }

  const isPublished = typeof workflow.version === "number"
  const online = (detail?.status ?? listed?.status) === "online"
  // Only a known missing scope disables the switch; a loading one does not.
  const lacksScope = listed ? canUpdate === false : canCreate === false
  const isPending = createFormIsPending || updateFormIsPending

  let hint = "Form is offline"
  if (!isPublished) {
    hint = "Publish the workflow to put its form online"
  } else if (online) {
    hint = "Form is online and accepting submissions"
  } else if (!listed) {
    hint = "Turn on to create a form from this workflow's inputs"
  }

  async function setOnline(next: boolean) {
    const status = next ? "online" : "offline"
    try {
      if (listed) {
        await updateForm({ status })
      } else {
        await createForm({ workflow_id: workflow.id, status })
      }
    } catch (error) {
      toast({
        title: next
          ? "Couldn't put the form online"
          : "Couldn't take the form offline",
        description:
          getFormErrorDetail(error)?.message ??
          getApiErrorDetail(error) ??
          "Something went wrong.",
        variant: "destructive",
      })
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 rounded-md border p-3">
        <div className="space-y-1">
          <Label
            htmlFor="form-trigger-switch"
            className="text-xs font-semibold"
          >
            Enable form
          </Label>
          <p className="text-xs text-muted-foreground">{hint}</p>
        </div>
        <Switch
          id="form-trigger-switch"
          checked={online}
          onCheckedChange={setOnline}
          className="data-[state=checked]:bg-emerald-500"
          disabled={!isPublished || lacksScope || isPending}
        />
      </div>

      {listed && (
        <>
          {detail && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Label
                  htmlFor="form-trigger-url"
                  className="text-xs font-medium"
                >
                  Requester link
                </Label>
                <CopyButton
                  value={detail.url}
                  toastMessage="Copied form link to clipboard"
                />
              </div>
              <Input
                id="form-trigger-url"
                value={detail.url}
                className="text-xs"
                readOnly
              />
            </div>
          )}
          <Link
            href={`/workspaces/${workspaceId}/forms/${listed.id}`}
            className="flex w-fit items-center gap-1.5 text-xs font-medium text-primary hover:underline"
          >
            <ExternalLinkIcon className="size-3.5" />
            Edit in Forms
          </Link>
        </>
      )}
    </div>
  )
}
