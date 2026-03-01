"use client"

import { useMemo } from "react"
import type { CaseRead, CaseTableRowRead, TableRowRead } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { AgGridTable } from "@/components/tables/ag-grid-table"
import { useGetTable } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CaseLinkedRowsSection({ caseData }: { caseData: CaseRead }) {
  const workspaceId = useWorkspaceId()
  const rows = (
    (caseData as CaseRead & { rows?: CaseTableRowRead[] }).rows ?? []
  ).filter((row) => row.row_data)

  const grouped = useMemo(() => {
    const map = new Map<
      string,
      { tableName: string; rows: CaseTableRowRead[] }
    >()
    for (const row of rows) {
      const existing = map.get(row.table_id)
      if (existing) {
        existing.rows.push(row)
        continue
      }
      map.set(row.table_id, {
        tableName: row.table_name ?? "Table",
        rows: [row],
      })
    }
    return Array.from(map.entries()).map(([tableId, value]) => ({
      tableId,
      tableName: value.tableName,
      rows: value.rows,
    }))
  }, [rows])

  if (grouped.length === 0) {
    return (
      <p className="p-2 text-sm text-muted-foreground">No linked table rows</p>
    )
  }

  return (
    <div className="space-y-4">
      {grouped.map((group) => (
        <LinkedTableGrid
          key={group.tableId}
          tableId={group.tableId}
          tableName={group.tableName}
          linkedRows={group.rows}
          workspaceId={workspaceId}
        />
      ))}
    </div>
  )
}

function LinkedTableGrid({
  tableId,
  tableName,
  linkedRows,
  workspaceId,
}: {
  tableId: string
  tableName: string
  linkedRows: CaseTableRowRead[]
  workspaceId: string
}) {
  const { table, tableIsLoading, tableError } = useGetTable({
    tableId,
    workspaceId,
  })

  const rowData = useMemo<TableRowRead[]>(() => {
    return linkedRows.map((row) => {
      const payload =
        row.row_data && typeof row.row_data === "object" ? row.row_data : {}
      return {
        ...(payload as Record<string, unknown>),
        id: row.row_id,
        created_at: row.created_at,
        updated_at: row.updated_at,
      }
    })
  }, [linkedRows])

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">{tableName}</p>
        <span className="text-xs text-muted-foreground">
          {rowData.length} linked row{rowData.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <div className="min-w-[1200px]">
          {tableIsLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="size-4" />
            </div>
          ) : tableError || !table ? (
            <div className="p-3 text-sm text-destructive">
              Failed to load table schema.
            </div>
          ) : (
            <AgGridTable
              table={table}
              rowsOverride={rowData}
              readOnly={true}
              hidePagination={true}
            />
          )}
        </div>
      </div>
    </div>
  )
}
