"use client"

import { PencilIcon, Trash2 } from "lucide-react"
import { useCallback, useState } from "react"
import { useForm } from "react-hook-form"
import type { TableColumnRead } from "@/client"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { Spinner } from "@/components/loading/spinner"
import { DynamicInput } from "@/components/tables/dynamic-column-input"
import { useTableSelection } from "@/components/tables/table-selection-context"
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
} from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { SqlType } from "@/lib/data-type"
import { useBatchDeleteRows, useBatchUpdateRows } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const SYSTEM_COLUMNS = new Set(["id", "created_at", "updated_at"])

export function TableSelectionActionsBar() {
  const workspaceId = useWorkspaceId()
  const { selectedCount, selectedRowIds, gridApi, tableId, columns } =
    useTableSelection()
  const { batchDeleteRows, batchDeleteRowsIsPending } = useBatchDeleteRows()
  const { batchUpdateRows, batchUpdateRowsIsPending } = useBatchUpdateRows()
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)

  const clearSelection = useCallback(() => {
    gridApi?.deselectAll()
  }, [gridApi])

  const handleDelete = useCallback(async () => {
    if (selectedRowIds.length === 0) return
    await batchDeleteRows({
      tableId,
      workspaceId,
      requestBody: { row_ids: selectedRowIds },
    })
    clearSelection()
    setConfirmDeleteOpen(false)
  }, [selectedRowIds, batchDeleteRows, tableId, workspaceId, clearSelection])

  if (!selectedCount || selectedCount === 0) {
    return null
  }

  const isBusy = batchDeleteRowsIsPending || batchUpdateRowsIsPending
  const userColumns = columns.filter((c) => !SYSTEM_COLUMNS.has(c.name))

  return (
    <>
      <span className="text-xs text-muted-foreground tabular-nums">
        {selectedCount} selected
      </span>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs text-muted-foreground"
        disabled={isBusy || userColumns.length === 0}
        onClick={() => setEditOpen(true)}
      >
        <PencilIcon className="mr-1 size-3" />
        Edit
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs text-destructive hover:text-destructive"
        disabled={isBusy}
        onClick={() => setConfirmDeleteOpen(true)}
      >
        <Trash2 className="mr-1 size-3" />
        Delete
      </Button>

      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm deletion</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete {selectedCount} selected
              {selectedCount === 1 ? " row" : " rows"}? This action cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={batchDeleteRowsIsPending}
              onClick={handleDelete}
            >
              {batchDeleteRowsIsPending ? (
                <span className="flex items-center">
                  <Spinner className="size-4" />
                  <span className="ml-2">Deleting...</span>
                </span>
              ) : (
                "Delete"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <BulkEditDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        columns={userColumns}
        selectedCount={selectedCount}
        selectedRowIds={selectedRowIds}
        tableId={tableId}
        workspaceId={workspaceId}
        batchUpdateRows={batchUpdateRows}
        batchUpdateRowsIsPending={batchUpdateRowsIsPending}
        clearSelection={clearSelection}
      />
    </>
  )
}

function BulkEditDialog({
  open,
  onOpenChange,
  columns,
  selectedCount,
  selectedRowIds,
  tableId,
  workspaceId,
  batchUpdateRows,
  batchUpdateRowsIsPending,
  clearSelection,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  columns: TableColumnRead[]
  selectedCount: number
  selectedRowIds: string[]
  tableId: string
  workspaceId: string
  batchUpdateRows: (params: {
    tableId: string
    workspaceId: string
    requestBody: { row_ids: string[]; data: Record<string, unknown> }
  }) => Promise<unknown>
  batchUpdateRowsIsPending: boolean
  clearSelection: () => void
}) {
  const [selectedColumn, setSelectedColumn] = useState<string>("")
  const form = useForm<Record<string, unknown>>({
    defaultValues: {},
  })

  const column = columns.find((c) => c.name === selectedColumn)

  const handleApply = async () => {
    if (!selectedColumn || !column) return
    const value = form.getValues(selectedColumn)
    if (selectedRowIds.length === 0) return

    await batchUpdateRows({
      tableId,
      workspaceId,
      requestBody: {
        row_ids: selectedRowIds,
        data: { [selectedColumn]: value },
      },
    })
    clearSelection()
    onOpenChange(false)
    setSelectedColumn("")
    form.reset()
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setSelectedColumn("")
      form.reset()
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {selectedCount} row(s)</DialogTitle>
          <DialogDescription>
            Select a column and set the new value to apply to all selected rows.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Column</label>
            <Select value={selectedColumn} onValueChange={setSelectedColumn}>
              <SelectTrigger>
                <SelectValue placeholder="Select a column" />
              </SelectTrigger>
              <SelectContent>
                {columns.map((col) => (
                  <SelectItem key={col.name} value={col.name}>
                    <span className="flex items-center gap-2">
                      {col.name}
                      <SqlTypeBadge type={col.type as SqlType} />
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {column && (
            <Form {...form}>
              <FormField
                control={form.control}
                name={selectedColumn}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>New value</FormLabel>
                    <FormControl>
                      <DynamicInput column={column} field={field} />
                    </FormControl>
                  </FormItem>
                )}
              />
            </Form>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleApply}
            disabled={!selectedColumn || batchUpdateRowsIsPending}
          >
            {batchUpdateRowsIsPending ? "Applying..." : "Apply"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
