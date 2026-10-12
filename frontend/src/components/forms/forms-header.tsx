"use client"

import { ExternalLinkIcon, PlusIcon } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import type { FormStatus } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CreateFormDialog } from "@/components/forms/create-form-dialog"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { WorkspaceResourceSyncActions } from "@/components/workspace-sync/resource-sync-actions"
import { useFormDetail, useUpdateForm } from "@/hooks/use-forms"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { getFormErrorDetail } from "@/lib/forms/errors"

/**
 * Header actions of the Forms list: push to Git and create a form.
 *
 * Forms have no sync resource of their own. They travel inside the workflow
 * file, so the push covers workflows.
 */
export function FormsListActions() {
  const access = useFormsAccess()
  const canCreate = useScopeCheck("form:create") === true
  const [createOpen, setCreateOpen] = useState(false)

  if (access !== "enabled") {
    return null
  }
  return (
    <>
      <WorkspaceResourceSyncActions
        label="workflows and forms"
        branchSlug="forms"
        resources={["workflow"]}
      />
      {canCreate && (
        <>
          <Button
            variant="outline"
            size="sm"
            className="h-7 bg-background"
            onClick={() => setCreateOpen(true)}
          >
            <PlusIcon className="mr-1 size-3.5" />
            New form
          </Button>
          <CreateFormDialog open={createOpen} onOpenChange={setCreateOpen} />
        </>
      )}
    </>
  )
}

function FormStatusSwitch({
  workspaceId,
  formId,
  status,
}: {
  workspaceId: string
  formId: string
  status: FormStatus
}) {
  const canEdit = useScopeCheck("form:update") === true
  const { updateForm, updateFormIsPending } = useUpdateForm({
    workspaceId,
    formId,
  })
  const online = status === "online"

  async function setOnline(next: boolean) {
    try {
      await updateForm({ status: next ? "online" : "offline" })
    } catch (error) {
      toast({
        title: next
          ? "Couldn't put the form online"
          : "Couldn't take the form offline",
        description:
          getFormErrorDetail(error)?.message ?? "Something went wrong.",
        variant: "destructive",
      })
    }
  }

  return (
    <div className="flex flex-none items-center gap-2 text-xs text-muted-foreground">
      <Switch
        checked={online}
        disabled={!canEdit || updateFormIsPending}
        aria-label="Form online"
        onCheckedChange={setOnline}
      />
      <span>{online ? "Online" : "Offline"}</span>
    </div>
  )
}

/**
 * Header title of the form detail page: "Forms / <title>" and the switch
 * that puts the form online or takes it offline.
 */
export function FormDetailTitle({
  workspaceId,
  formId,
}: {
  workspaceId: string
  formId: string
}) {
  const access = useFormsAccess()
  const enabled = access === "enabled"
  const { form, formIsLoading } = useFormDetail(
    { workspaceId, formId },
    { enabled }
  )

  if (!enabled) {
    return <h1 className="text-sm font-semibold">Forms</h1>
  }
  let page = "Form not found"
  if (form) {
    page = form.config.title || form.workflow_title
  }
  return (
    <div className="flex min-w-0 items-center gap-3">
      <Breadcrumb className="min-w-0">
        <BreadcrumbList className="flex min-w-0 flex-nowrap items-center gap-2 overflow-hidden whitespace-nowrap text-sm">
          <BreadcrumbItem>
            <BreadcrumbLink
              asChild
              className="font-semibold hover:no-underline"
            >
              <Link href={`/workspaces/${workspaceId}/forms`}>Forms</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator className="shrink-0">
            <span className="text-muted-foreground">/</span>
          </BreadcrumbSeparator>
          <BreadcrumbItem className="min-w-0">
            {formIsLoading ? (
              <Skeleton className="h-4 w-28" />
            ) : (
              <BreadcrumbPage className="truncate font-semibold">
                {page}
              </BreadcrumbPage>
            )}
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      {form && (
        <FormStatusSwitch
          workspaceId={workspaceId}
          formId={formId}
          status={form.status}
        />
      )}
    </div>
  )
}

/** Header actions of the form detail page: open the form as a requester. */
export function FormDetailActions({
  workspaceId,
  formId,
}: {
  workspaceId: string
  formId: string
}) {
  const access = useFormsAccess()
  const enabled = access === "enabled"
  const { form } = useFormDetail({ workspaceId, formId }, { enabled })

  if (!enabled || !form) {
    return null
  }
  return (
    <Button
      asChild
      variant="outline"
      size="sm"
      className="h-7 gap-1.5 bg-background"
    >
      <a href={`/forms/${form.id}`} target="_blank" rel="noopener noreferrer">
        <ExternalLinkIcon className="size-3.5" />
        Open
      </a>
    </Button>
  )
}
