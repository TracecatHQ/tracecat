"use client"

import type React from "react"
import { useState } from "react"
import type { WorkflowReadMinimal } from "@/client"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { AlertDialogTrigger } from "@/components/ui/alert-dialog"
import { useWorkflowManager } from "@/lib/hooks"

export function DeleteWorkflowConfirmDialog({
  open,
  onOpenChange,
  workflowId,
  workflowTitle,
  onDeleted,
  children,
}: React.PropsWithChildren<{
  open: boolean
  onOpenChange: (open: boolean) => void
  workflowId: string | null
  workflowTitle?: string | null
  onDeleted?: () => void
}>) {
  const { deleteWorkflow, deleteWorkflowIsPending } = useWorkflowManager(
    undefined,
    { listEnabled: false }
  )
  const id = workflowId ?? ""

  return (
    <ConfirmDestructiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Delete workflow"
      description={
        <>
          Permanently delete{" "}
          {workflowTitle ? (
            <span className="font-medium text-foreground">{workflowTitle}</span>
          ) : (
            "this workflow"
          )}
          ? This also deletes its schedules. This cannot be undone.
        </>
      }
      identifier={{ label: "Workflow ID", value: id }}
      confirmPhrase={id}
      isPending={deleteWorkflowIsPending}
      onConfirm={async () => {
        if (!workflowId) return
        await deleteWorkflow(workflowId)
        onOpenChange(false)
        onDeleted?.()
      }}
    >
      {children}
    </ConfirmDestructiveDialog>
  )
}

export function DeleteWorkflowAlertDialog({
  selectedWorkflow,
  setSelectedWorkflow,
  children,
}: React.PropsWithChildren<{
  selectedWorkflow: WorkflowReadMinimal | null
  setSelectedWorkflow: (selectedWorkflow: WorkflowReadMinimal | null) => void
}>) {
  const [open, setOpen] = useState(false)
  return (
    <DeleteWorkflowConfirmDialog
      open={open && selectedWorkflow !== null}
      onOpenChange={(isOpen) => {
        setOpen(isOpen)
        if (!isOpen) {
          setSelectedWorkflow(null)
        }
      }}
      workflowId={selectedWorkflow?.id ?? null}
      workflowTitle={selectedWorkflow?.title}
    >
      {children}
    </DeleteWorkflowConfirmDialog>
  )
}

export const DeleteWorkflowAlertDialogTrigger = AlertDialogTrigger
