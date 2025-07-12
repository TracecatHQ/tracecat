"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { Row } from "@tanstack/react-table"
import { useRouter } from "next/navigation"
import { useCallback, useState } from "react"
import type { TableReadMinimal } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { TableActions } from "@/components/tables/table-actions"
import { DeleteTableDialog } from "@/components/tables/table-delete-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useListTables } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

export function TablesDashboard() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { tables, tablesIsLoading, tablesError } = useListTables({
    workspaceId,
  })
  const [selectedTable, setSelectedTable] = useState<TableReadMinimal | null>(
    null
  )
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const basePath = `/workspaces/${workspaceId}/tables`
  const handleOnClickRow = useCallback(
    (row: Row<TableReadMinimal>) => () =>
      router.push(`${basePath}/${row.original.id}`),
    [router, basePath]
  )

  const handleDeleteClick = useCallback((table: TableReadMinimal) => {
    setSelectedTable(table)
    setDeleteDialogOpen(true)
  }, [])

  return (
    <>
      {selectedTable && (
        <DeleteTableDialog
          table={selectedTable}
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
        />
      )}
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
          {
            id: "actions",
            enableHiding: false,
            cell: ({ row }) => {
              return (
                <div className="flex justify-end">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        className="size-6 p-0"
                        onClick={(e) => e.stopPropagation()} // Prevent row click
                      >
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <TableActions
                        table={row.original}
                        onDeleteClick={handleDeleteClick}
                      />
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )
            },
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<TableReadMinimal> = {
  filterProps: {
    placeholder: "Search tables...",
    column: "name",
  },
}
