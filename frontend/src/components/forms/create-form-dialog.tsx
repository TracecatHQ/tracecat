"use client"

import { WorkflowIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo } from "react"
import type { FormReadMinimal, WorkflowReadMinimal } from "@/client"
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
import { toast } from "@/components/ui/use-toast"
import { useCreateForm, useForms } from "@/hooks/use-forms"
import { getApiErrorDetail } from "@/lib/errors"
import { getFormErrorDetail } from "@/lib/forms/errors"
import { workflowShortIdToUuid } from "@/lib/forms/workflow-id"
import { useWorkflowManager } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

/** What the new-form dialog says about a workflow, and whether it can be picked. */
export interface FormWorkflowHint {
  /** Shown next to the workflow, or `null` for nothing. */
  text: string | null
  /** Whether the workflow cannot get a new form. */
  disabled: boolean
}

/**
 * Whether a workflow can get a new form, and the hint to show for it.
 *
 * A workflow has at most one form. A form can be prepared on a workflow that
 * was never published, from its draft inputs; it only cannot go online until
 * the first publish.
 */
export function getFormWorkflowHint(
  workflow: Pick<WorkflowReadMinimal, "id" | "version">,
  forms: readonly Pick<FormReadMinimal, "workflow_id">[]
): FormWorkflowHint {
  const workflowUuid = workflowShortIdToUuid(workflow.id) ?? workflow.id
  if (forms.some((form) => form.workflow_id === workflowUuid)) {
    return { text: "Already has a form", disabled: true }
  }
  if (workflow.version === null || workflow.version === undefined) {
    return { text: "Offline until the workflow is published", disabled: false }
  }
  return { text: null, disabled: false }
}

/**
 * Pick the workflow a new form starts. The form is created offline with an
 * empty config, and the builder lands on its detail page.
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
  const { workflows, workflowsLoading } = useWorkflowManager(undefined, {
    listEnabled: open,
  })
  const { forms, formsIsLoading } = useForms(workspaceId, { enabled: open })
  const { createForm, createFormIsPending } = useCreateForm(workspaceId)

  const rows = useMemo(
    () =>
      (workflows ?? []).map((workflow) => ({
        workflow,
        hint: getFormWorkflowHint(workflow, forms),
      })),
    [workflows, forms]
  )
  const isLoading = workflowsLoading || formsIsLoading

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
      <DialogContent className="gap-0 overflow-hidden p-0 sm:max-w-lg">
        <DialogHeader className="border-b px-4 py-3">
          <DialogTitle className="text-sm">New form</DialogTitle>
          <DialogDescription className="text-xs">
            Pick the workflow this form starts. Its inputs become the form's
            fields.
          </DialogDescription>
        </DialogHeader>
        <Command>
          <CommandInput placeholder="Search workflows..." />
          <CommandList className="max-h-80">
            <CommandEmpty>
              {isLoading ? "Loading workflows..." : "No workflows found."}
            </CommandEmpty>
            {!isLoading && rows.length > 0 && (
              <CommandGroup>
                {rows.map(({ workflow, hint }) => (
                  <CommandItem
                    key={workflow.id}
                    value={`${workflow.title} ${workflow.alias ?? ""} ${workflow.id}`}
                    disabled={hint.disabled || createFormIsPending}
                    onSelect={() => handleSelect(workflow)}
                    className="text-xs"
                  >
                    <WorkflowIcon className="text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate">
                      {workflow.title}
                    </span>
                    {hint.text && (
                      <span className="flex-none text-muted-foreground">
                        {hint.text}
                      </span>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
