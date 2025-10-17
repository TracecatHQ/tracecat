"use client"

import {
  ChevronDownIcon,
  CopyIcon,
  DatabaseZapIcon,
  Trash2Icon,
} from "lucide-react"
import { useParams } from "next/navigation"
import { useState } from "react"
import { z } from "zod"
import type { TableColumnRead } from "@/client"
import { Spinner } from "@/components/loading/spinner"
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { useDeleteColumn, useUpdateColumn } from "@/lib/hooks"
import { SqlTypeCreatableEnum } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

type TableViewColumnMenuType = "delete" | "edit" | "set-natural-key" | null

export function TableViewColumnMenu({ column }: { column: TableColumnRead }) {
  const { user } = useAuth()
  const params = useParams<{ tableId?: string }>()
  const tableId = params?.tableId
  const [activeType, setActiveType] = useState<TableViewColumnMenuType>(null)
  const onOpenChange = () => setActiveType(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="size-4 p-0 !ring-0">
            <span className="sr-only">Configure column</span>
            <ChevronDownIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(column.id))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy ID
          </DropdownMenuItem>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(column.name))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy name
          </DropdownMenuItem>
          {user?.isPrivileged() && (
            <>
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("set-natural-key")
                }}
                disabled={column.is_index}
              >
                <DatabaseZapIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                {column.is_index ? "Unique index" : "Create unique index"}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="py-1 text-xs text-destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("delete")
                }}
              >
                <Trash2Icon className="mr-2 size-3 group-hover/item:text-destructive" />
                Delete column
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <TableColumnDeleteDialog
        tableId={tableId}
        column={column}
        open={activeType === "delete"}
        onOpenChange={onOpenChange}
      />
      <TableColumnIndexDialog
        tableId={tableId}
        column={column}
        open={activeType === "set-natural-key"}
        onOpenChange={onOpenChange}
      />
    </>
  )
}

function TableColumnDeleteDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { deleteColumn } = useDeleteColumn()
  const [confirmName, setConfirmName] = useState("")

  if (!tableId || !workspaceId) {
    return null
  }

  const handleDeleteColumn = async () => {
    if (confirmName !== column.name) {
      toast({
        title: "Column name does not match",
        description: "Please type the exact column name to confirm deletion",
      })
      return
    }

    try {
      await deleteColumn({
        tableId,
        workspaceId,
        columnId: column.id,
      })
      onOpenChange()
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete column permanently</AlertDialogTitle>
          <AlertDialogDescription>
            To confirm deletion, type the column name <b>{column.name}</b>{" "}
            below. This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${column.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDeleteColumn}
            variant="destructive"
            disabled={confirmName !== column.name}
          >
            Delete column
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const _updateColumnSchema = z.object({
  name: z
    .string()
    .min(1, { message: "Name must be at least 1 character" })
    .max(255, { message: "Name must be less than 255 characters" })
    .regex(/^[a-zA-Z0-9_]+$/, {
      message: "Name must contain only letters, numbers, and underscores",
    }),
  type: z.enum(SqlTypeCreatableEnum),
  nullable: z.boolean(),
})

function TableColumnIndexDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { updateColumn, updateColumnIsPending } = useUpdateColumn()

  if (!tableId || !workspaceId) {
    return null
  }

  if (column.is_index) {
    return (
      <AlertDialog open={open} onOpenChange={onOpenChange}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Column is already a unique index
            </AlertDialogTitle>
            <AlertDialogDescription>
              Column <b>{column.name}</b> is already a unique index.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Close</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }

  const handleSetIndex = async () => {
    try {
      const updates = {
        is_index: true,
      }

      await updateColumn({
        tableId,
        columnId: column.id,
        workspaceId,
        requestBody: updates,
      })

      toast({
        title: "Created unique index",
        description: "Column is now a unique index.",
      })

      onOpenChange()
    } catch (error) {
      console.error("Error creating unique index:", error)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={() => {
        if (!updateColumnIsPending) {
          onOpenChange()
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Create unique index</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to make column <b>{column.name}</b> a unique
            index? This enables upsert operations on the table.
            <br />
            <br />
            <strong>Requirements:</strong>
            <ul className="mt-2 list-disc pl-5 text-xs">
              <li>All values in the column must be unique</li>
              <li>This cannot be undone except by recreating the column</li>
            </ul>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={updateColumnIsPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleSetIndex}
            disabled={updateColumnIsPending}
          >
            {updateColumnIsPending ? (
              <>
                <Spinner />
                Creating...
              </>
            ) : (
              <>
                <DatabaseZapIcon className="mr-2 size-4" />
                Create unique index
              </>
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
