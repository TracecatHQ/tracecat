"use client"

import { useRouter } from "next/navigation"
import { useState } from "react"
import type { TableReadMinimal } from "@/client"
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
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useDeleteTable } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function DeleteTableDialog({
  table,
  open,
  onOpenChange,
}: {
  table: TableReadMinimal
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { deleteTable } = useDeleteTable()
  const [confirmName, setConfirmName] = useState("")
  if (!workspaceId) {
    return null
  }

  // Clear the confirmation input whenever the dialog closes so a value typed
  // for one table doesn't carry over to the next table's delete dialog.
  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setConfirmName("")
    }
    onOpenChange(nextOpen)
  }

  const handleDeleteTable = async () => {
    if (confirmName !== table.name) {
      toast({
        title: "Table name does not match",
        description: "Please type the exact table name to confirm deletion",
      })
      return
    }
    try {
      await deleteTable({
        tableId: table.id,
        workspaceId,
      })
      handleOpenChange(false)
      router.push(`/workspaces/${workspaceId}/tables`)
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete Table</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete the table {table.name}? This action
            cannot be undone.
          </AlertDialogDescription>
          <AlertDialogDescription>Table ID: {table.id}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${table.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDeleteTable}
            variant="destructive"
            disabled={confirmName !== table.name}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
