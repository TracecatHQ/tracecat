"use client"

import type { TableRead } from "@/client"
import { AgGridTable } from "@/components/tables/ag-grid-table"
import { TableSearchProvider } from "@/components/tables/table-search-context"

export function DatabaseTable({ table }: { table: TableRead }) {
  return (
    <TableSearchProvider key={table.id} tableId={table.id}>
      <AgGridTable table={table} />
    </TableSearchProvider>
  )
}
