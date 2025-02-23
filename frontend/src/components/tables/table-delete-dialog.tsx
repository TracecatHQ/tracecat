"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { TableReadMinimal } from "@/client"
import { useWorkspace } from "@/providers/workspace"

import { useDeleteTable } from "@/lib/hooks"
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
  const { workspaceId } = useWorkspace()
  const { deleteTable } = useDeleteTable()
  const [confirmName, setConfirmName] = useState("")
  if (!workspaceId) {
    return null
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
      onOpenChange(false)
      router.push(`/workspaces/${workspaceId}/tables`)
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
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
