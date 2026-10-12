"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { format } from "date-fns"
import {
  ClipboardListIcon,
  CopyIcon,
  ExternalLinkIcon,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import type { FormReadMinimal, FormStatus } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CatalogHeader } from "@/components/catalog/catalog-header"
import { DeleteFormDialog } from "@/components/forms/delete-form-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useForms } from "@/hooks/use-forms"
import { formWorkflowHref } from "@/lib/forms/workflow-id"
import { cn, shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const BADGE_CLASS = "h-5 px-2 text-[10px] font-normal"

/** A form's status as a badge with a coloured dot. */
export function FormStatusBadge({ status }: { status: FormStatus }) {
  const online = status === "online"
  return (
    <Badge variant="secondary" className={cn(BADGE_CLASS, "gap-1.5")}>
      <span
        aria-hidden
        className={cn(
          "size-2 rounded-full",
          online ? "bg-emerald-500" : "bg-muted-foreground/40"
        )}
      />
      {online ? "Online" : "Offline"}
    </Badge>
  )
}

function FormRow({
  form,
  workspaceId,
  canDelete,
  onDelete,
}: {
  form: FormReadMinimal
  workspaceId: string
  canDelete: boolean
  onDelete: (form: FormReadMinimal) => void
}) {
  const requesterPath = `/forms/${form.id}`
  const lastSubmittedAt = form.last_submitted_at
    ? new Date(form.last_submitted_at)
    : null

  function copyLink() {
    const url = `${window.location.origin}${requesterPath}`
    void navigator.clipboard.writeText(url)
    toast({ title: "Form link copied", description: url })
  }

  return (
    <li className="flex items-center gap-3 border-b border-border/50 px-3 py-1.5 transition-colors hover:bg-muted/50">
      <span className="flex size-7 flex-none items-center justify-center">
        <ClipboardListIcon className="size-4 text-muted-foreground" />
      </span>
      <div className="min-w-0 flex-1">
        <Link
          href={`/workspaces/${workspaceId}/forms/${form.id}`}
          className="block truncate text-xs font-medium hover:underline focus-visible:underline focus-visible:outline-none"
        >
          {form.title}
        </Link>
        <Link
          href={formWorkflowHref(workspaceId, form.workflow_id)}
          className="flex w-fit max-w-full items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <WorkflowIcon className="size-3 flex-none" />
          <span className="truncate">{form.workflow_title}</span>
        </Link>
      </div>
      <div className="flex flex-none items-center gap-2">
        <FormStatusBadge status={form.status} />
        {typeof form.submission_count === "number" && (
          <Badge variant="secondary" className={BADGE_CLASS}>
            {form.submission_count === 1
              ? "1 submission"
              : `${form.submission_count.toLocaleString()} submissions`}
          </Badge>
        )}
        {lastSubmittedAt && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="secondary"
                className={cn(BADGE_CLASS, "cursor-default")}
              >
                {shortTimeAgo(lastSubmittedAt)}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              Last submitted {format(lastSubmittedAt, "PPpp")}
            </TooltipContent>
          </Tooltip>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="size-7 p-0">
              <span className="sr-only">Open menu for {form.title}</span>
              <DotsHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild className="text-xs">
              <a href={requesterPath} target="_blank" rel="noopener noreferrer">
                <ExternalLinkIcon className="mr-2 size-3" />
                Open form
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem className="text-xs" onClick={copyLink}>
              <CopyIcon className="mr-2 size-3" />
              Copy link
            </DropdownMenuItem>
            {canDelete && (
              <DropdownMenuItem
                className="text-xs text-destructive focus:text-destructive"
                onClick={() => onDelete(form)}
              >
                <Trash2Icon className="mr-2 size-3" />
                Delete
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  )
}

/**
 * The builder's list of forms in a workspace: status, the workflow each one
 * starts, and how much it is used. Search filters by form or workflow name.
 */
export function FormsDashboard() {
  const workspaceId = useWorkspaceId()
  const canDelete = useScopeCheck("form:delete") === true
  const { forms, formsIsLoading, formsError } = useForms(workspaceId)
  const [searchQuery, setSearchQuery] = useState("")
  const [formToDelete, setFormToDelete] = useState<FormReadMinimal | null>(null)

  const search = searchQuery.trim().toLowerCase()
  const filteredForms = useMemo(
    () =>
      forms.filter(
        (form) =>
          search.length === 0 ||
          form.title.toLowerCase().includes(search) ||
          form.workflow_title.toLowerCase().includes(search)
      ),
    [forms, search]
  )

  if (formsIsLoading) {
    return <CenteredSpinner />
  }
  if (formsError) {
    return (
      <div className="p-6">
        <AlertNotification level="error" message="Error loading forms." />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {formToDelete && (
        <DeleteFormDialog
          form={formToDelete}
          open
          onOpenChange={(open) => {
            if (!open) {
              setFormToDelete(null)
            }
          }}
        />
      )}
      <CatalogHeader
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search forms..."
        displayCount={filteredForms.length}
        countLabel={filteredForms.length === 1 ? "form" : "forms"}
      />
      <div className="min-h-0 flex-1 overflow-auto">
        {filteredForms.length === 0 ? (
          <div className="flex h-full p-6">
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <ClipboardListIcon className="size-5 text-muted-foreground/60" />
                </EmptyMedia>
                <EmptyTitle>
                  {search ? "No forms found" : "No forms yet"}
                </EmptyTitle>
                <EmptyDescription>
                  {search
                    ? "No forms match your search."
                    : "Create a form to let people start a workflow by filling in its inputs."}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          </div>
        ) : (
          <ul className="w-full pb-10">
            {filteredForms.map((form) => (
              <FormRow
                key={form.id}
                form={form}
                workspaceId={workspaceId}
                canDelete={canDelete}
                onDelete={setFormToDelete}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
