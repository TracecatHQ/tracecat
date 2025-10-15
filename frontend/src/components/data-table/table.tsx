"use client"

import {
  type Column,
  type ColumnDef,
  type ColumnFiltersState,
  flexRender,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type Row,
  type SortingState,
  type TableState,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table"
import { AlertTriangleIcon } from "lucide-react"
import Link from "next/link"
import * as React from "react"
import AuxClickMenu, {
  type AuxClickMenuOptionProps,
} from "@/components/aux-click-menu"
import {
  DataTablePagination,
  DataTableToolbar,
  type ServerSidePaginationProps,
} from "@/components/data-table"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { cn } from "@/lib/utils"

import type { DataTableToolbarProps } from "./toolbar"

type ColumnMeta = {
  headerClassName?: string
  headerStyle?: React.CSSProperties
  cellClassName?: string
  cellStyle?: React.CSSProperties
}

export type TableCol<TData> = {
  table: ReturnType<typeof useReactTable<TData>>
  column: Column<TData>
}
interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[]
  data?: TData[]
  onClickRow?: (row: Row<TData>) => () => void
  getRowHref?: (row: Row<TData>) => string | undefined
  toolbarProps?: DataTableToolbarProps<TData>
  tableHeaderAuxOptions?: AuxClickMenuOptionProps<TableCol<TData>>[]
  isLoading?: boolean
  error?: Error | null
  emptyMessage?: string
  errorMessage?: string
  showSelectedRows?: boolean
  initialSortingState?: SortingState
  initialColumnVisibility?: VisibilityState
  tableId?: string
  onDeleteRows?: (selectedRows: Row<TData>[]) => Promise<void> | void
  onSelectionChange?: (selectedRows: Row<TData>[]) => void
  serverSidePagination?: ServerSidePaginationProps
  clearSelectionTrigger?: number
}

export function DataTable<TData, TValue>({
  columns,
  data,
  onClickRow,
  getRowHref,
  toolbarProps,
  tableHeaderAuxOptions,
  isLoading,
  error,
  emptyMessage,
  errorMessage,
  showSelectedRows = false,
  initialSortingState: initialSorting = [],
  initialColumnVisibility,
  tableId,
  onDeleteRows,
  onSelectionChange,
  serverSidePagination,
  clearSelectionTrigger,
}: DataTableProps<TData, TValue>) {
  const [tableState, setTableState] = useLocalStorage<Partial<TableState>>(
    `table-state:${tableId}`,
    {
      sorting: initialSorting,
      columnVisibility: initialColumnVisibility,
    }
  )

  const [columnFilters, setColumnFilters] = React.useState<ColumnFiltersState>(
    tableState.columnFilters ?? []
  )
  const [rowSelection, setRowSelection] = React.useState(
    tableState.rowSelection ?? {}
  )
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>(tableState.columnVisibility ?? {})
  const [sorting, setSorting] = React.useState<SortingState>(
    tableState.sorting ?? []
  )

  React.useEffect(() => {
    if (tableId) {
      setTableState({
        ...tableState,
        columnFilters,
        sorting,
        rowSelection,
        columnVisibility,
      })
    }
  }, [columnFilters, sorting, rowSelection, columnVisibility])

  const table = useReactTable({
    data: data || [],
    columns,
    state: {
      sorting,
      columnVisibility,
      rowSelection,
      columnFilters,
      ...(serverSidePagination && {
        pagination: {
          pageIndex: serverSidePagination.currentPage,
          pageSize: serverSidePagination.pageSize,
        },
      }),
    },
    initialState: {
      pagination: {
        pageSize: serverSidePagination?.pageSize ?? 10,
        pageIndex: serverSidePagination?.currentPage ?? 0,
      },
    },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
    manualPagination: !!serverSidePagination,
    pageCount: serverSidePagination ? -1 : undefined,
  })

  // Notify parent of selection changes
  React.useEffect(() => {
    if (onSelectionChange) {
      const selectedRows = table.getFilteredSelectedRowModel().rows
      onSelectionChange(selectedRows)
    }
  }, [rowSelection, onSelectionChange, table])

  React.useEffect(() => {
    if (clearSelectionTrigger === undefined) return
    setRowSelection({})
  }, [clearSelectionTrigger])

  // Handle initial sync when data is first loaded
  const [hasData, setHasData] = React.useState(false)
  React.useEffect(() => {
    if (data && data.length > 0 && !hasData) {
      setHasData(true)
      if (onSelectionChange && Object.keys(rowSelection).length > 0) {
        // Force a sync of the initial selection
        const selectedRows = table.getFilteredSelectedRowModel().rows
        onSelectionChange(selectedRows)
      }
    }
  }, [data, hasData, rowSelection, onSelectionChange, table])

  return (
    <div>
      <div className="space-y-4">
        {toolbarProps && (
          <DataTableToolbar
            table={table}
            {...toolbarProps}
            onDeleteRows={onDeleteRows}
          />
        )}
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => {
                    const columnMeta = header.column.columnDef.meta as
                      | ColumnMeta
                      | undefined

                    return (
                      <AuxClickMenu
                        key={header.id}
                        options={tableHeaderAuxOptions}
                        data={{ table, column: header.column }}
                      >
                        <TableHead
                          colSpan={header.colSpan}
                          className={cn(
                            header.column.id?.toString().toLowerCase() ===
                              "actions" && "text-right",
                            columnMeta?.headerClassName
                          )}
                          style={columnMeta?.headerStyle}
                        >
                          {header.isPlaceholder
                            ? null
                            : header.column.id?.toString().toLowerCase() ===
                                "actions"
                              ? null
                              : flexRender(
                                  header.column.columnDef.header,
                                  header.getContext()
                                )}
                        </TableHead>
                      </AuxClickMenu>
                    )
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              <TableContents
                isLoading={isLoading}
                error={error}
                table={table}
                colSpan={columns.length}
                onClickRow={onClickRow}
                getRowHref={getRowHref}
                emptyMessage={emptyMessage}
                errorMessage={errorMessage}
                pageSize={
                  serverSidePagination?.pageSize ??
                  table.getState().pagination.pageSize
                }
              />
            </TableBody>
          </Table>
        </div>
        <DataTablePagination
          table={table}
          showSelectedRows={showSelectedRows}
          serverSide={serverSidePagination}
        />
      </div>
    </div>
  )
}

function TableContents<TData>({
  isLoading,
  error,
  table,
  colSpan,
  onClickRow,
  getRowHref,
  emptyMessage = "No results.",
  errorMessage = "Failed to fetch data",
  pageSize,
}: {
  isLoading?: boolean
  error?: Error | null
  table: ReturnType<typeof useReactTable<TData>>
  colSpan: number
  onClickRow?: (row: Row<TData>) => () => void
  getRowHref?: (row: Row<TData>) => string | undefined
  emptyMessage?: string
  errorMessage?: string
  pageSize?: number
}) {
  if (isLoading) {
    // Show skeleton rows equivalent to page size
    const skeletonRowCount = pageSize || 10
    return (
      <>
        {Array.from({ length: skeletonRowCount }).map((_, index) => (
          <TableRow key={`skeleton-${index}`}>
            {Array.from({ length: colSpan }).map((_, cellIndex) => (
              <TableCell key={`skeleton-cell-${cellIndex}`} className="py-3">
                <Skeleton className="h-4 w-full" />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </>
    )
  }
  if (error) {
    return (
      <TableRow>
        <TableCell
          colSpan={colSpan}
          className="font-sm h-24 text-center text-xs text-muted-foreground"
        >
          <div className="flex items-center justify-center">
            <AlertTriangleIcon className="mr-2 size-4 fill-rose-500 stroke-white" />
            <span>{errorMessage}</span>
          </div>
        </TableCell>
      </TableRow>
    )
  }

  if (table.getRowModel().rows?.length === 0) {
    return (
      <TableRow>
        <TableCell
          colSpan={colSpan}
          className="font-sm h-24 text-center text-xs text-muted-foreground"
        >
          {emptyMessage}
        </TableCell>
      </TableRow>
    )
  }
  return (
    <>
      {table.getRowModel().rows.map((row) => {
        const href = getRowHref?.(row)

        return (
          <TableRow
            key={row.id}
            data-state={row.getIsSelected() && "selected"}
            onClick={!href ? onClickRow?.(row) : undefined}
            className="cursor-pointer"
          >
            {row.getVisibleCells().map((cell) => {
              const isActionsCol =
                cell.column.id?.toString().toLowerCase() === "actions"
              const columnMeta = cell.column.columnDef.meta as
                | ColumnMeta
                | undefined
              const cellClassName = cn(
                columnMeta?.cellClassName,
                isActionsCol && "p-2"
              )

              const content = flexRender(
                cell.column.columnDef.cell,
                cell.getContext()
              )

              // For action columns, don't wrap in Link
              if (isActionsCol || !href) {
                return (
                  <TableCell
                    key={cell.id}
                    className={cellClassName}
                    style={columnMeta?.cellStyle}
                  >
                    {isActionsCol ? (
                      <div className="flex justify-end">{content}</div>
                    ) : (
                      content
                    )}
                  </TableCell>
                )
              }

              // For regular cells with href, wrap content in Link
              return (
                <TableCell
                  key={cell.id}
                  className={cellClassName}
                  style={columnMeta?.cellStyle}
                >
                  <Link href={href} prefetch={false} className="block -m-2 p-2">
                    {content}
                  </Link>
                </TableCell>
              )
            })}
          </TableRow>
        )
      })}
    </>
  )
}
