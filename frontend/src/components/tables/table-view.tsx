"use client"

import React from "react"
import { useParams } from "next/navigation"
import { TableColumnRead, TableRead, TableRowRead } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { CellContext, ColumnDef } from "@tanstack/react-table"

import { useListRows } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DataTable } from "@/components/data-table"
import { JsonViewWithControls } from "@/components/json-viewer"
import { TableViewAction } from "@/components/tables/table-view-action"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"
import { KeyIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

function CollapsibleText({ text }: { text: string }) {
  const [isExpanded, setIsExpanded] = React.useState(false);

  if (!isExpanded) {
    return (
      <div className="flex items-center">
        <span className="text-xs truncate">
          {text.substring(0, 25)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIsExpanded(true)}
          className="h-6 px-1 text-xs text-muted-foreground hover:bg-transparent"
        >
          ...
        </Button>
      </div>
    );
  }

  // Format the text into chunks when expanded
  const chunks = [];
  for (let i = 0; i < text.length; i += 25) {
    chunks.push(text.substring(i, i + 25));
  }

  return (
    <div className="space-y-1">
      <pre className="text-xs whitespace-pre-wrap">{chunks.join('\n')}</pre>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setIsExpanded(false)}
        className="h-6 px-2 text-xs text-muted-foreground"
      >
        Collapse
      </Button>
    </div>
  );
}

function CollapsibleText({ text }: { text: string }) {
  const [isExpanded, setIsExpanded] = React.useState(false)

  if (!isExpanded) {
    return (
      <div className="flex items-center">
        <span className="truncate text-xs">{text.substring(0, 25)}</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIsExpanded(true)}
          className="h-6 px-1 text-xs text-muted-foreground hover:bg-transparent"
        >
          ...
        </Button>
      </div>
    )
  }

  // Format the text into chunks when expanded
  const chunks = []
  for (let i = 0; i < text.length; i += 25) {
    chunks.push(text.substring(i, i + 25))
  }

  return (
    <div className="space-y-1">
      <pre className="whitespace-pre-wrap text-xs">{chunks.join("\n")}</pre>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setIsExpanded(false)}
        className="h-6 px-2 text-xs text-muted-foreground"
      >
        Collapse
      </Button>
    </div>
  )
}

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
      header: () => {
        const is_natural_key = column.is_natural_key;

        return (
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold text-foreground/90">
              {column.name}
            </span>
            <span className="lowercase text-muted-foreground">{column.type}</span>
            {is_natural_key && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-100">
                <KeyIcon className="mr-1 h-3 w-3" />
                Key
              </span>
            )}
            <TableViewColumnMenu column={column} />
          </div>
        );
      },
      cell: ({ row }: CellT) => {
        const value = row.original[column.name as keyof TableRowRead];
        return (
          <div className="text-xs w-full">
            {typeof value === "object" && value ? (
              <div onClick={(e) => e.stopPropagation()} className="w-full">
                <TooltipProvider>
                  <JsonViewWithControls
                    src={value}
                  />
                </TooltipProvider>
              </div>
            ) : typeof value === "string" && value.length > 25 ? (
              <CollapsibleText text={String(value)} />
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
