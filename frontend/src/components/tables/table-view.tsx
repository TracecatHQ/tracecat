"use client"

import type { TableRead } from "@/client"
import { AgGridTable } from "@/components/tables/ag-grid-table"
import { TableSearchProvider } from "@/components/tables/table-search-context"
import { TableSearchStatus } from "@/components/tables/table-search-status"

export function DatabaseTable({ table }: { table: TableRead }) {
  return (
    <TableSearchProvider key={table.id} tableId={table.id}>
      <div className="flex h-full min-h-0 flex-col gap-2">
        <TableSearchStatus />
        <div className="min-h-0 flex-1">
          <AgGridTable table={table} />
        </div>
      </div>
    </TableSearchProvider>
  )
}
