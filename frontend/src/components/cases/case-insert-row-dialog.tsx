"use client"

import type { TableColumnRead } from "@/client"
import {
  type TableRowFormData,
  TableRowFormDialog,
} from "@/components/tables/table-insert-row-dialog"
import { toast } from "@/components/ui/use-toast"
import { useInsertCaseRow } from "@/hooks/use-case-rows"

/** Props for {@link CaseInsertRowDialog}. */
export interface CaseInsertRowDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  workspaceId: string
  tableId: string
  tableName: string | null
  /** Table schema from the case-scoped summary, not a `table:read`. */
  columns: readonly TableColumnRead[]
}

/**
 * The tables view's "Add new row" form on the case page: the new row is
 * inserted into the table and linked to the case in one request.
 */
export function CaseInsertRowDialog({
  open,
  onOpenChange,
  caseId,
  workspaceId,
  tableId,
  tableName,
  columns,
}: CaseInsertRowDialogProps) {
  const { insertCaseRow, insertCaseRowIsPending } = useInsertCaseRow({
    caseId,
    workspaceId,
  })

  async function handleSubmit(data: TableRowFormData) {
    await insertCaseRow({ tableId, data })
    toast({
      title: "Row added",
      description: "Added the row and linked it to this case.",
    })
  }

  let description = "Add a new row to this table and link it to this case."
  if (tableName) {
    description = `Add a new row to the "${tableName}" table and link it to this case.`
  }

  return (
    <TableRowFormDialog
      open={open}
      onOpenChange={onOpenChange}
      columns={columns}
      tableName={tableName}
      description={description}
      onSubmit={handleSubmit}
      isPending={insertCaseRowIsPending}
    />
  )
}
