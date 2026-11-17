"use client"

import type { TableColumnRead, TableRowRead } from "@/client"
import {
  type TableRowFormData,
  TableRowFormDialog,
} from "@/components/tables/table-insert-row-dialog"
import { toast } from "@/components/ui/use-toast"
import { useUpdateCaseRow } from "@/hooks/use-case-rows"

/** Props for {@link CaseEditRowDialog}. */
export interface CaseEditRowDialogProps {
  /** The row to edit; `null` closes the dialog. */
  row: TableRowRead | null
  onOpenChange: (open: boolean) => void
  caseId: string
  workspaceId: string
  tableId: string
  tableName: string | null
  /** Table schema from the case-scoped summary, not a `table:read`. */
  columns: readonly TableColumnRead[]
}

/**
 * The row form on the case page, filled with a linked row's values. Saving
 * writes the changed columns to the table row through `table:update`.
 */
export function CaseEditRowDialog({
  row,
  onOpenChange,
  caseId,
  workspaceId,
  tableId,
  tableName,
  columns,
}: CaseEditRowDialogProps) {
  const { updateCaseRow, updateCaseRowIsPending } = useUpdateCaseRow({
    caseId,
    workspaceId,
  })

  async function handleSubmit(data: TableRowFormData) {
    if (!row) return
    await updateCaseRow({ tableId, rowId: row.id, data })
    toast({ title: "Row updated" })
  }

  return (
    <TableRowFormDialog
      open={row !== null}
      onOpenChange={onOpenChange}
      columns={columns}
      tableName={tableName}
      description={
        tableName
          ? `Edit this row of the "${tableName}" table.`
          : "Edit this row."
      }
      onSubmit={handleSubmit}
      isPending={updateCaseRowIsPending}
      initialRow={row ?? undefined}
    />
  )
}
