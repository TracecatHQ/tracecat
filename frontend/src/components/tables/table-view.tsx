"use client"

import type { TableRead } from "@/client"
import { AgGridTable } from "@/components/tables/ag-grid-table"

export function DatabaseTable({ table }: { table: TableRead }) {
  return <AgGridTable table={table} />
}
