"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { Copy, Unlink } from "lucide-react"
import { useMemo, useState } from "react"
import type { CaseTableRowRead } from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { UnlinkCaseTableRowDialog } from "./unlink-case-table-row-dialog"

interface CaseTableRowsTableProps {
  rows: CaseTableRowRead[]
  isLoading: boolean
  error: Error | null
  caseId: string
  workspaceId: string
  pagination: {
    currentPage: number
    hasNextPage: boolean
    hasPreviousPage: boolean
    pageSize: number
    totalEstimate: number
    startItem: number
    endItem: number
    onNextPage: () => void
    onPreviousPage: () => void
    onFirstPage: () => void
    onPageSizeChange: (size: number) => void
    isLoading: boolean
  }
  onRefetch?: () => void
}

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

const HIDDEN_FIELDS = new Set(["id", "created_at", "updated_at"])

function formatCellValue(value: JsonValue): string {
  if (value === null || value === undefined) return "\u2014"
  if (Array.isArray(value)) return value.join(", ")
  if (typeof value === "object") return JSON.stringify(value)
  if (typeof value === "boolean") return value ? "true" : "false"
  return String(value)
}

export function CaseTableRowsTable({
  rows,
  isLoading,
  error,
  caseId,
  workspaceId,
  pagination,
  onRefetch,
}: CaseTableRowsTableProps) {
  const [rowToUnlink, setRowToUnlink] = useState<CaseTableRowRead | null>(null)
  const [unlinkDialogOpen, setUnlinkDialogOpen] = useState(false)

  const dynamicColumns = useMemo(() => {
    const keys = new Set<string>()
    for (const row of rows) {
      for (const key of Object.keys(row.row_data || {})) {
        if (!HIDDEN_FIELDS.has(key)) keys.add(key)
      }
    }
    return Array.from(keys).sort()
  }, [rows])

  const rowDataColumns: ColumnDef<CaseTableRowRead>[] =
    dynamicColumns.length > 0
      ? dynamicColumns.map((key) => ({
          id: `row-${key}`,
          header: ({ column }) => (
            <DataTableColumnHeader
              column={column}
              title={key}
              className="text-xs"
            />
          ),
          cell: ({ row }) => (
            <span className="block truncate text-xs text-muted-foreground">
              {formatCellValue(row.original.row_data?.[key] as JsonValue)}
            </span>
          ),
          enableSorting: false,
          enableHiding: false,
        }))
      : [
          {
            id: "row-data-fallback",
            header: ({ column }) => (
              <DataTableColumnHeader
                column={column}
                title="Row data"
                className="text-xs"
              />
            ),
            cell: ({ row }) => (
              <span className="block truncate text-xs text-muted-foreground">
                {formatCellValue(row.original.row_data as JsonValue)}
              </span>
            ),
            enableSorting: false,
            enableHiding: false,
          },
        ]

  const columns: ColumnDef<CaseTableRowRead>[] = [
    {
      accessorKey: "table_name",
      header: ({ column }) => (
        <DataTableColumnHeader
          column={column}
          title="Table"
          className="text-xs"
        />
      ),
      cell: ({ row }) => (
        <span className="text-sm font-medium text-foreground">
          {row.original.table_name}
        </span>
      ),
      enableSorting: false,
      enableHiding: false,
    },
    ...rowDataColumns,
    {
      id: "actions",
      enableHiding: false,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className="size-8 p-0"
                onClick={(event) => event.stopPropagation()}
              >
                <span className="sr-only">Open menu</span>
                <DotsHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  navigator.clipboard?.writeText(String(row.original.row_id))
                }}
              >
                <Copy className="mr-2 h-3 w-3" />
                Copy ID
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  setRowToUnlink(row.original)
                  setUnlinkDialogOpen(true)
                }}
              >
                <Unlink className="mr-2 h-3 w-3 text-rose-600" />
                <span className="text-rose-600">Unlink</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  if (!isLoading && !error && rows.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
        No rows added
      </div>
    )
  }

  return (
    <>
      <DataTable<CaseTableRowRead, unknown>
        data={rows}
        columns={columns}
        isLoading={isLoading}
        error={error}
        emptyMessage="No table rows linked"
        tableId={`${caseId}-table-rows`}
        serverSidePagination={{
          currentPage: pagination.currentPage,
          hasNextPage: pagination.hasNextPage,
          hasPreviousPage: pagination.hasPreviousPage,
          pageSize: pagination.pageSize,
          totalEstimate: pagination.totalEstimate,
          startItem: pagination.startItem,
          endItem: pagination.endItem,
          onNextPage: pagination.onNextPage,
          onPreviousPage: pagination.onPreviousPage,
          onFirstPage: pagination.onFirstPage,
          onPageSizeChange: pagination.onPageSizeChange,
          isLoading: pagination.isLoading,
        }}
      />

      <UnlinkCaseTableRowDialog
        open={unlinkDialogOpen}
        onOpenChange={(open) => {
          setUnlinkDialogOpen(open)
          if (!open) {
            setRowToUnlink(null)
          }
        }}
        caseId={caseId}
        workspaceId={workspaceId}
        linkId={rowToUnlink?.id ?? null}
        tableName={rowToUnlink?.table_name ?? ""}
        rowId={rowToUnlink?.row_id ?? ""}
        onSuccess={() => {
          onRefetch?.()
          setRowToUnlink(null)
        }}
      />
    </>
  )
}
