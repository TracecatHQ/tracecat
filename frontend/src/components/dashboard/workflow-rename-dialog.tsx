"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkflowReadMinimal } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useWorkflowManager } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const renameWorkflowSchema = z.object({
  title: z
    .string()
    .trim()
    .min(3, "Name must be at least 3 characters")
    .max(100, "Name cannot exceed 100 characters"),
})

type RenameWorkflowSchema = z.infer<typeof renameWorkflowSchema>

/**
 * Dialog for renaming a workflow's title from the workflows dashboard.
 *
 * Reuses the existing `updateWorkflow` mutation and mirrors the folder rename
 * flow so the two entities behave consistently.
 */
export function WorkflowRenameDialog({
  open,
  onOpenChange,
  selectedWorkflow,
  setSelectedWorkflow,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedWorkflow: WorkflowReadMinimal | null
  setSelectedWorkflow: (workflow: WorkflowReadMinimal | null) => void
}) {
  const workspaceId = useWorkspaceId()
  const { updateWorkflow, updateWorkflowIsPending } = useWorkflowManager(
    undefined,
    { listEnabled: false }
  )

  const form = useForm<RenameWorkflowSchema>({
    resolver: zodResolver(renameWorkflowSchema),
    defaultValues: {
      title: selectedWorkflow?.title || "",
    },
  })

  // Sync the form when a different workflow is selected
  useEffect(() => {
    if (selectedWorkflow) {
      form.reset({
        title: selectedWorkflow.title,
      })
    }
  }, [selectedWorkflow, form])

  const onSubmit = async (data: RenameWorkflowSchema) => {
    if (!selectedWorkflow) return
    if (data.title === selectedWorkflow.title) {
      setSelectedWorkflow(null)
      onOpenChange(false)
      return
    }

    try {
      await updateWorkflow({
        workflowId: selectedWorkflow.id,
        workspaceId,
        requestBody: {
          title: data.title,
        },
      })
      toast({
        title: "Workflow renamed",
        description: `Renamed to "${data.title}".`,
      })
      setSelectedWorkflow(null)
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to rename workflow:", error)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedWorkflow(null)
        }
        onOpenChange(isOpen)
      }}
    >
      <DialogContent>
        <DialogHeader className="space-y-4">
          <DialogTitle>Rename workflow</DialogTitle>
          <DialogDescription>
            Enter a new name for the workflow.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="flex items-center gap-2 text-xs">
                    Name
                  </FormLabel>
                  <FormControl>
                    <Input autoFocus {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={updateWorkflowIsPending}>
                {updateWorkflowIsPending && <Spinner className="mr-2 size-4" />}
                Rename workflow
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
