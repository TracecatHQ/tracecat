"use client"

import { SearchXIcon } from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import type { FormRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { FormActivityTab } from "@/components/forms/form-activity-tab"
import { FormConfigTab } from "@/components/forms/form-config-tab"
import { FormPreviewPane } from "@/components/forms/form-preview-pane"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFormConfigCheck, useFormConfigDraft } from "@/hooks/use-form-config"
import { useFormDetail, useSavedFormPreview } from "@/hooks/use-forms"
import { getFormLoadFailure } from "@/lib/forms/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Tabs of the form detail page, as they appear in `?tab=`. */
export type FormDetailTab = "config" | "activity"

const TAB_TRIGGER_CLASS =
  "flex h-full items-center justify-center rounded-none px-3 py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none"

function FormDetailBody({ form }: { form: FormRead }) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const canEditScope = useScopeCheck("form:update")
  const canEdit = canEditScope === true
  const canReadSubmissions = useScopeCheck("form:submission:read") === true
  const tab: FormDetailTab =
    searchParams?.get("tab") === "activity" ? "activity" : "config"

  // The buffer lives here, above the tabs, so it survives a visit to
  // Activity and feeds the preview on the right.
  const draft = useFormConfigDraft(form)
  const check = useFormConfigCheck({
    workspaceId: form.workspace_id,
    workflowId: form.workflow_id,
    text: draft.text,
    enabled: canEdit,
  })
  // A viewer cannot run that check, so they get the saved config's preview.
  // Only a known missing scope asks for it, not one that is still loading.
  const saved = useSavedFormPreview(
    {
      workspaceId: form.workspace_id,
      formId: form.id,
      updatedAt: form.updated_at,
    },
    { enabled: canEditScope === false }
  )

  function selectTab(next: string) {
    const params = new URLSearchParams(searchParams?.toString())
    if (next === "activity") {
      params.set("tab", "activity")
    } else {
      params.delete("tab")
    }
    const query = params.toString()
    router.replace(query ? `${pathname}?${query}` : (pathname ?? ""))
  }

  return (
    <ResizablePanelGroup direction="horizontal" className="h-full">
      <ResizablePanel defaultSize={55} minSize={30}>
        <Tabs
          value={tab}
          onValueChange={selectTab}
          className="flex h-full min-h-0 flex-col"
        >
          <TabsList className="h-8 w-full flex-none justify-start rounded-none border-b bg-transparent p-0">
            <TabsTrigger value="config" className={TAB_TRIGGER_CLASS}>
              Config
            </TabsTrigger>
            <TabsTrigger value="activity" className={TAB_TRIGGER_CLASS}>
              Activity
            </TabsTrigger>
          </TabsList>
          <TabsContent value="config" className="mt-0 min-h-0 flex-1">
            <FormConfigTab
              form={form}
              canEdit={canEdit}
              draft={draft}
              check={check}
            />
          </TabsContent>
          <TabsContent value="activity" className="mt-0 min-h-0 flex-1">
            <FormActivityTab
              form={form}
              canReadSubmissions={canReadSubmissions}
              preview={canEdit ? check.preview : saved.preview}
            />
          </TabsContent>
        </Tabs>
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel defaultSize={45} minSize={25}>
        <FormPreviewPane
          form={form}
          check={check}
          isDirty={draft.isDirty}
          canEdit={canEdit}
          savedPreview={saved.preview}
          savedPreviewFailed={saved.previewFailed}
        />
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

/**
 * The builder's page for one form: the config editor and Activity on the
 * left, a live preview on the right.
 *
 * The server answers 404 for a form that does not exist and for a caller
 * who can neither edit forms nor read submissions; both show "Form not
 * found".
 */
export function FormDetail({ formId }: { formId: string }) {
  const workspaceId = useWorkspaceId()
  const { form, formIsLoading, formError } = useFormDetail({
    workspaceId,
    formId,
  })

  if (formIsLoading) {
    return <CenteredSpinner />
  }
  if (formError || !form) {
    if (formError && getFormLoadFailure(formError) !== "not_found") {
      return (
        <div className="p-6">
          <AlertNotification level="error" message="Error loading the form." />
        </div>
      )
    }
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <SearchXIcon className="size-5" />
          </EmptyMedia>
          <EmptyTitle>Form not found</EmptyTitle>
          <EmptyDescription>
            It may have been deleted, or you may not have access to it.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline" size="sm">
            <Link href={`/workspaces/${workspaceId}/forms`}>Back to forms</Link>
          </Button>
        </EmptyContent>
      </Empty>
    )
  }
  // A different form is a different buffer.
  return <FormDetailBody key={form.id} form={form} />
}
