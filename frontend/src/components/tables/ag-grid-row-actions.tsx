"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { CustomCellRendererProps } from "ag-grid-react"
import { CopyIcon, Trash2Icon } from "lucide-react"
import { useParams } from "next/navigation"
import { useState } from "react"
import type { TableRowRead } from "@/client"
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
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/hooks/use-auth"
import { useDeleteRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

type RowActionType = "delete" | null

export function AgGridRowActions(params: CustomCellRendererProps) {
  const { user } = useAuth()
  const [activeType, setActiveType] = useState<RowActionType>(null)
  const row = params.data as TableRowRead

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="size-4 p-0">
            <span className="sr-only">Open menu</span>
            <DotsHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(row.id))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy ID
          </DropdownMenuItem>
          {user?.isPrivileged() && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="py-1 text-xs text-destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("delete")
                }}
              >
                <Trash2Icon className="mr-2 size-3 group-hover/item:text-destructive" />
                Delete
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <RowDeleteDialog
        row={row}
        open={activeType === "delete"}
        onOpenChange={() => setActiveType(null)}
      />
    </>
  )
}

function RowDeleteDialog({
  row,
  open,
  onOpenChange,
}: {
  row: TableRowRead
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const params = useParams<{ tableId?: string }>()
  const tableId = params?.tableId
  const workspaceId = useWorkspaceId()
  const { deleteRow } = useDeleteRow()

  if (!tableId || !workspaceId) {
    return null
  }

  const handleDeleteRow = async () => {
    try {
      await deleteRow({
        tableId,
        workspaceId,
        rowId: row.id,
      })
      onOpenChange(false)
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete row</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this row from the table? This action
            cannot be undone.
          </AlertDialogDescription>
          <AlertDialogDescription>Row ID: {row.id}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={handleDeleteRow}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
