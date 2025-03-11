"use client"

import React from "react"
import { useParams } from "next/navigation"
import { TableColumnRead, TableRead, TableRowRead } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { CellContext, ColumnDef } from "@tanstack/react-table"

import { useListRows } from "@/lib/hooks"
import { DataTable } from "@/components/data-table"
import { TableViewAction } from "@/components/tables/table-view-action"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"

export function DatabaseTable({ table: { columns } }: { table: TableRead }) {
  const { tableId } = useParams<{ tableId: string }>()
  const { workspaceId } = useWorkspace()
  const { rows, rowsIsLoading, rowsError } = useListRows({
    tableId,
    workspaceId,
  })

  type CellT = CellContext<TableRowRead, TableColumnRead>
  const allColumns: ColumnDef<TableRowRead, TableColumnRead>[] = [
    ...columns.map((column) => ({
      accessorKey: column.name,
      header: () => (
        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-foreground/90">
            {column.name}
          </span>
          <span className="lowercase text-muted-foreground">{column.type}</span>
          <TableViewColumnMenu column={column} />
        </div>
      ),
      cell: ({ row }: CellT) => {
        const value = row.original[column.name as keyof TableRowRead];
        const [isExpanded, setIsExpanded] = React.useState(false);

        const formattedJson = value && typeof value === "object" ? JSON.stringify(value, null, 2) : "";
        const lines = formattedJson.split('\n');
        const hasMoreThan15Lines = lines.length > 15;

        return (
          <div className="text-xs w-full">
            {typeof value === "object" ? (
              <div className="relative w-full">
                {value && hasMoreThan15Lines && (
                  <button
                    className="absolute right-1 top-1 p-1 rounded-full bg-slate-200 hover:bg-slate-300 z-10"
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsExpanded(!isExpanded);
                    }}
                  >
                    {isExpanded ? "↑" : "↓"}
                  </button>
                )}
                <pre className="text-xs overflow-hidden">
                  {value && formatJsonWithLimit(value, isExpanded ? undefined : 15)}
                </pre>
              </div>
            ) : (
              <pre className="text-xs">{String(value)}</pre>
            )}
          </div>
        )
      },
      enableSorting: true,
      enableHiding: true,
    })),
    {
      id: "actions",
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }: CellT) => <TableViewAction row={row} />,
    },
  ]

  return (
    <DataTable<TableRowRead, TableColumnRead>
      isLoading={rowsIsLoading}
      error={rowsError ?? undefined}
      data={rows}
      emptyMessage="No rows found."
      errorMessage="Error loading rows."
      columns={allColumns}
    />
  )
}

function formatJsonWithLimit(jsonValue: any, lineLimit?: number): string {
  const formattedJson = JSON.stringify(jsonValue, null, 2);

  if (!lineLimit) {
    return formattedJson;
  }

  const lines = formattedJson.split('\n');
  if (lines.length <= lineLimit) {
    return formattedJson;
  }

  const limitedLines = lines.slice(0, lineLimit);
  return limitedLines.join('\n') + '\n...';
}
