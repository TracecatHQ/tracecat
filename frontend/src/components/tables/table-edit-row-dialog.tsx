"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { Row } from "@tanstack/react-table"
import { useParams } from "next/navigation"
import { useEffect, useMemo } from "react"
import { useForm } from "react-hook-form"
import type { TableRead, TableRowRead } from "@/client"
import {
  buildInitialValues,
  createRowSchema,
  type DynamicFormData,
  TableRowFieldInput,
  TableRowFieldLabel,
} from "@/components/tables/table-row-form"
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
import { useGetTable, useUpdateTableRow } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function TableEditRowDialog({
  open,
  onOpenChange,
  row,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  row: Row<TableRowRead>
}) {
  const params = useParams<{ tableId: string }>()
  const tableId = params?.tableId
  const workspaceId = useWorkspaceId()
  const { table } = useGetTable({ tableId: tableId || "", workspaceId })
  const { updateRow, updateRowIsPending } = useUpdateTableRow()

  if (!tableId || !table || !workspaceId) {
    return null
  }

  return (
    <TableEditRowDialogContent
      open={open}
      onOpenChange={onOpenChange}
      row={row}
      table={table}
      tableId={tableId}
      workspaceId={workspaceId}
      updateRow={updateRow}
      updateRowIsPending={updateRowIsPending}
    />
  )
}

function TableEditRowDialogContent({
  open,
  onOpenChange,
  row,
  table,
  tableId,
  workspaceId,
  updateRow,
  updateRowIsPending,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  row: Row<TableRowRead>
  table: TableRead
  tableId: string
  workspaceId: string
  updateRow: ReturnType<typeof useUpdateTableRow>["updateRow"]
  updateRowIsPending: boolean
}) {
  const schema = useMemo(() => createRowSchema(table), [table])

  const initialValues = useMemo(
    () => buildInitialValues(table, row.original),
    [table, row.original]
  )

  const form = useForm<DynamicFormData>({
    resolver: zodResolver(schema),
    defaultValues: initialValues,
  })

  useEffect(() => {
    if (open) {
      form.reset(initialValues)
    }
  }, [open, initialValues, form])

  const rowId = row.original.id

  const onSubmit = async (data: DynamicFormData) => {
    try {
      if (!rowId) {
        console.error("Row ID is missing")
        return
      }
      await updateRow({
        requestBody: { data },
        tableId,
        rowId,
        workspaceId,
      })
      onOpenChange(false)
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit row</DialogTitle>
          <DialogDescription>
            Update the selected row in the "{table.name}" table (ID: {rowId}).
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            {table.columns.map((column) => (
              <FormField
                key={column.id}
                control={form.control}
                name={column.name}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      <TableRowFieldLabel column={column} />
                    </FormLabel>
                    <FormControl>
                      <TableRowFieldInput column={column} field={field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ))}
            <DialogFooter>
              <Button type="submit" disabled={updateRowIsPending}>
                Save changes
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
