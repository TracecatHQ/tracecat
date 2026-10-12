"use client"

import { useState } from "react"
import { FormRenderer } from "@/components/forms/form-renderer"
import { requesterStyles } from "@/components/forms/form-styles"
import { RequesterPage } from "@/components/forms/requester/requester-shell"
import {
  FormNotFoundState,
  FormOfflineState,
  RequesterErrorState,
} from "@/components/forms/requester/requester-states"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"
import { useRenderedForm, useSubmitForm } from "@/hooks/use-forms"
import { useRequesterForm } from "@/hooks/use-requester-forms"
import { canSubmitForms } from "@/lib/forms/access"
import {
  getFormErrorCode,
  getFormLoadFailure,
  isFormOfflineError,
} from "@/lib/forms/errors"
import type { FormValues } from "@/lib/forms/visibility"
import { useUserScopes } from "@/lib/hooks"

/**
 * The fill-in page for one form.
 *
 * A requester arrives with a form ID and no workspace, so this locates the
 * form first, then renders it, then submits to it. It shows the form's
 * confirmation after a submission and never the workflow run behind it.
 */
export function RequesterForm({ formId }: { formId: string }) {
  const { user } = useAuth()
  const {
    location,
    locationIsLoading,
    locationError,
    refetch: refetchLocation,
  } = useRequesterForm(formId)
  const workspaceId = location?.workspace_id
  const {
    form,
    formIsLoading,
    formError,
    refetch: refetchForm,
  } = useRenderedForm({ workspaceId, formId })
  const { userScopes, isLoading: scopesIsLoading } = useUserScopes(
    workspaceId,
    { enabled: Boolean(workspaceId) }
  )
  const { submitForm } = useSubmitForm({
    workspaceId: workspaceId ?? "",
    formId,
  })
  // Set when a submission finds the form offline after it was rendered.
  const [wentOffline, setWentOffline] = useState(false)

  async function handleSubmit(values: FormValues) {
    try {
      return await submitForm({
        values,
        definition_version: form?.definition_version,
      })
    } catch (error) {
      if (getFormErrorCode(error) === "form_changed") {
        // Load the new version. The renderer keeps the answers that still
        // apply and tells the requester the form was updated.
        await refetchForm()
      } else if (isFormOfflineError(error)) {
        setWentOffline(true)
      }
      throw error
    }
  }

  function renderBody() {
    if (locationIsLoading) {
      return <CenteredSpinner />
    }
    if (locationError || !workspaceId) {
      return getFormLoadFailure(locationError) === "not_found" ? (
        <FormNotFoundState />
      ) : (
        <RequesterErrorState onRetry={() => refetchLocation()} />
      )
    }
    if (wentOffline) {
      return <FormOfflineState />
    }
    if (formIsLoading || scopesIsLoading) {
      return <CenteredSpinner />
    }
    if (formError) {
      const failure = getFormLoadFailure(formError)
      if (failure === "not_found") {
        return <FormNotFoundState />
      }
      if (failure === "offline") {
        return <FormOfflineState />
      }
    }
    if (!form) {
      return <RequesterErrorState onRetry={() => refetchForm()} />
    }
    return (
      <div className={requesterStyles.formColumn}>
        <FormRenderer
          formId={formId}
          form={form}
          mode="live"
          // Unknown scopes do not block the form: the server decides.
          canSubmit={userScopes ? canSubmitForms(userScopes.scopes) : true}
          onSubmit={handleSubmit}
          submitterEmail={user?.email}
        />
      </div>
    )
  }

  return (
    <RequesterPage title={location?.workspace_name ?? "Forms"}>
      {renderBody()}
    </RequesterPage>
  )
}
