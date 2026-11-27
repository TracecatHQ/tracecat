"use client"

import { ClipboardListIcon, WorkflowIcon } from "lucide-react"
import Link from "next/link"
import { useMemo } from "react"
import type { FormReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { TriggerStatusLabel } from "@/components/triggers/trigger-status"
import { useForms } from "@/hooks/use-forms"
import { useFormsAccess } from "@/hooks/use-forms-access"

const NO_FORMS: FormReadMinimal[] = []

/**
 * The workspace's forms, for the Triggers tab. Forms are not triggers: they
 * come from the Forms list as it is, with its entitlement and scopes, and are
 * managed on the Forms page only.
 *
 * Only forms the caller can open on the Forms page are kept. Without
 * `form:update` or `form:submission:read` the list hides each form's
 * workflow and the Forms page won't open the form, so none are listed.
 */
export function useFormTriggers(workspaceId: string) {
  const canReadForms = useScopeCheck("form:read")
  const access = useFormsAccess()
  const enabled = canReadForms === true && access === "enabled"
  const { forms, formsIsLoading, formsError } = useForms(workspaceId, {
    enabled,
  })
  const listed = useMemo(
    () => forms.filter((form) => form.workflow_id && form.workflow_title),
    [forms]
  )
  return {
    forms: enabled ? listed : NO_FORMS,
    isLoading: enabled && formsIsLoading,
    error: enabled ? formsError : null,
  }
}

/**
 * Read-only rows for forms, in the trigger list's columns. Each links to the
 * form's page, where it is edited and switched on or off.
 */
export function FormTriggerRows({
  workspaceId,
  forms,
  error,
}: {
  workspaceId: string
  forms: FormReadMinimal[]
  error: unknown
}) {
  if (forms.length === 0 && !error) {
    return null
  }
  return (
    <>
      <div className="px-4 py-2 text-xs text-muted-foreground">
        Forms start their workflow on each submission. They are managed on the
        Forms page.
      </div>
      {forms.map((form) => (
        <FormTriggerRow key={form.id} workspaceId={workspaceId} form={form} />
      ))}
      {error ? (
        <p className="px-4 py-2 text-xs text-destructive">
          Couldn't load forms.
        </p>
      ) : null}
    </>
  )
}

function FormTriggerRow({
  workspaceId,
  form,
}: {
  workspaceId: string
  form: FormReadMinimal
}) {
  return (
    <Link
      href={`/workspaces/${workspaceId}/forms/${form.id}`}
      className="flex items-center gap-2 px-4 py-2 text-xs transition-colors hover:bg-muted/50"
    >
      <ClipboardListIcon className="size-4 shrink-0 text-muted-foreground" />
      <span className="w-[220px] shrink-0 truncate">{form.title}</span>
      <span className="w-[120px] shrink-0 truncate text-muted-foreground">
        Form
      </span>
      <span className="w-[220px] shrink-0 truncate text-muted-foreground">
        Form submissions
      </span>
      <span className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
        <span
          className="inline-flex min-w-0 items-center gap-1 rounded border px-1.5 py-0.5"
          title={form.workflow_title ?? undefined}
        >
          <WorkflowIcon className="size-3 shrink-0" />
          <span className="truncate">{form.workflow_title}</span>
        </span>
      </span>
      <span className="w-16 shrink-0">
        <TriggerStatusLabel status={form.status} />
      </span>
    </Link>
  )
}
