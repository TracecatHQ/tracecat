"use client"

import type { Row } from "@tanstack/react-table"
import { useRouter } from "next/navigation"
import { useCallback } from "react"
import type { TableReadMinimal } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"

import { useListTables } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

export function TablesDashboard() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { tables, tablesIsLoading, tablesError } = useListTables({
    workspaceId,
  })
  const basePath = `/workspaces/${workspaceId}/tables`
  const handleOnClickRow = useCallback(
    (row: Row<TableReadMinimal>) => () =>
      router.push(`${basePath}/${row.original.id}`),
    [router, basePath]
  )

  return (
    <DataTable
      isLoading={tablesIsLoading}
      error={tablesError ?? undefined}
      data={tables}
      emptyMessage="No tables found."
      errorMessage="Error loading tables."
      onClickRow={handleOnClickRow}
      columns={[
        {
          accessorKey: "name",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Name"
            />
          ),
          cell: ({ row }) => (
            <div className="text-xs text-foreground/80">
              {row.getValue<TableReadMinimal["name"]>("name")}
            </div>
          ),
          enableSorting: true,
          enableHiding: false,
        },
      ]}
      toolbarProps={defaultToolbarProps}
    />
  )
}

const defaultToolbarProps: DataTableToolbarProps<TableReadMinimal> = {
  filterProps: {
    placeholder: "Search tables...",
    column: "name",
  },
}
