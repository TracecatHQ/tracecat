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
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import type { FormReadMinimal, WorkflowReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CatalogHeader } from "@/components/catalog/catalog-header"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { useCreateForm, useDeleteForm, useForms } from "@/hooks/use-forms"
import { getApiErrorDetail } from "@/lib/errors"
import { getFormErrorDetail } from "@/lib/forms/errors"
import { useWorkflowManager } from "@/lib/hooks"
import { cn, copyToClipboard, shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const BADGE_CLASS = "h-5 px-2 text-[10px] font-normal"

/**
 * Pick the workflow a new form starts (one form per workflow; an unpublished
 * workflow's form stays offline). The form is created offline with an empty
 * config, and the builder lands on its detail page.
 */
export function CreateFormDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { workflows, workflowsLoading, workflowsError } = useWorkflowManager(
    undefined,
    { listEnabled: open }
  )
  const { forms, formsIsLoading } = useForms(workspaceId, { enabled: open })
  const { mutateAsync: createForm, isPending: createFormIsPending } =
    useCreateForm(workspaceId)
  const taken = new Set(forms.map((form) => form.workflow_id))
  const isLoading = workflowsLoading || formsIsLoading
  let emptyMessage = "No workflows found."
  if (workflowsError) {
    emptyMessage = "Couldn't load workflows."
  } else if (isLoading) {
    emptyMessage = "Loading workflows..."
  }

  async function handleSelect(workflow: WorkflowReadMinimal) {
    try {
      const form = await createForm({ workflow_id: workflow.id })
      onOpenChange(false)
      router.push(`/workspaces/${workspaceId}/forms/${form.id}`)
    } catch (error) {
      toast({
        title: "Couldn't create the form",
        description:
          getFormErrorDetail(error)?.message ??
          getApiErrorDetail(error) ??
          "Something went wrong.",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Create form</DialogTitle>
          <DialogDescription>
            Pick the workflow this form starts. Its inputs become the form's
            fields.
          </DialogDescription>
        </DialogHeader>
        <Command className="min-h-0 flex-1 rounded-none">
          <CommandInput placeholder="Search workflows..." />
          <CommandList className="max-h-none flex-1">
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            {!isLoading && (workflows ?? []).length > 0 && (
              <CommandGroup>
                {(workflows ?? []).map((workflow) => {
                  let hint: string | null = null
                  if (taken.has(workflow.id)) {
                    hint = "Already has a form"
                  } else if (typeof workflow.version !== "number") {
                    hint = "Offline until the workflow is published"
                  }
                  return (
                    <CommandItem
                      key={workflow.id}
                      value={`${workflow.title} ${workflow.alias ?? ""} ${workflow.id}`}
                      disabled={taken.has(workflow.id) || createFormIsPending}
                      onSelect={() => handleSelect(workflow)}
                      className="text-xs"
                    >
                      <WorkflowIcon className="text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate">
                        {workflow.title}
                      </span>
                      {hint && (
                        <span className="flex-none text-muted-foreground">
                          {hint}
                        </span>
                      )}
                    </CommandItem>
                  )
                })}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Confirm deleting a form. Its submissions go with it; the workflow and the
 * runs it already started stay. The dialog closes only once that succeeds.
 */
function DeleteFormDialog({
  form,
  onClose,
}: {
  form: FormReadMinimal
  onClose: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { mutateAsync: deleteForm, isPending: deleteFormIsPending } =
    useDeleteForm(workspaceId)

  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete form</AlertDialogTitle>
          <AlertDialogDescription>
            Delete {form.title}? Its submissions and activity are deleted with
            it. The workflow and its past runs are not affected. This cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={deleteFormIsPending}
            onClick={(event) => {
              event.preventDefault()
              // A failure is already reported by the mutation; stay open.
              deleteForm(form.id).then(onClose, () => {})
            }}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

async function copyFormLink(formId: string) {
  const url = `${window.location.origin}/forms/${formId}`
  if (await copyToClipboard({ value: url, message: "Form link copied" })) {
    toast({ title: "Form link copied", description: url })
  } else {
    // The link stays in the toast so it can be copied by hand.
    toast({
      title: "Couldn't copy the link",
      description: url,
      variant: "destructive",
    })
  }
}

/**
 * The builder's list of forms in a workspace: status, the workflow each one
 * starts, and how much it is used. Search filters by form or workflow name.
 */
export function FormsDashboard() {
  const workspaceId = useWorkspaceId()
  const canDelete = useScopeCheck("form:delete") === true
  const canReadWorkflows = useScopeCheck("workflow:read") === true
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
          form.workflow_title?.toLowerCase().includes(search)
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
          onClose={() => setFormToDelete(null)}
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
            {filteredForms.map((form) => {
              const lastSubmittedAt = form.last_submitted_at
                ? new Date(form.last_submitted_at)
                : null
              return (
                <li
                  key={form.id}
                  className="flex items-center gap-3 border-b border-border/50 px-3 py-1.5 transition-colors hover:bg-muted/50"
                >
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
                    {form.workflow_id &&
                      (canReadWorkflows ? (
                        <Link
                          href={`/workspaces/${workspaceId}/workflows/${form.workflow_id}`}
                          className="flex w-fit max-w-full items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                        >
                          <WorkflowIcon className="size-3 flex-none" />
                          <span className="truncate">
                            {form.workflow_title}
                          </span>
                        </Link>
                      ) : (
                        <span className="flex w-fit max-w-full items-center gap-1 text-[11px] text-muted-foreground">
                          <WorkflowIcon className="size-3 flex-none" />
                          <span className="truncate">
                            {form.workflow_title}
                          </span>
                        </span>
                      ))}
                  </div>
                  <div className="flex flex-none items-center gap-2">
                    <Badge
                      variant="secondary"
                      className={cn(BADGE_CLASS, "gap-1.5")}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "size-2 rounded-full",
                          form.status === "online"
                            ? "bg-emerald-500"
                            : "bg-muted-foreground/40"
                        )}
                      />
                      {form.status === "online" ? "Online" : "Offline"}
                    </Badge>
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
                        <Button
                          variant="ghost"
                          size="sm"
                          className="size-7 p-0"
                        >
                          <span className="sr-only">
                            Open menu for {form.title}
                          </span>
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem asChild className="text-xs">
                          <a
                            href={`/forms/${form.id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <ExternalLinkIcon className="mr-2 size-3" />
                            Open form
                          </a>
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-xs"
                          onClick={() => copyFormLink(form.id)}
                        >
                          <CopyIcon className="mr-2 size-3" />
                          Copy link
                        </DropdownMenuItem>
                        {canDelete && (
                          <DropdownMenuItem
                            className="text-xs text-destructive focus:text-destructive"
                            onClick={() => setFormToDelete(form)}
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
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
