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
import { useLocalStorage } from "@/lib/hooks"
import { cn } from "@/lib/utils"

import type { DataTableToolbarProps } from "./toolbar"

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
  onDeleteRows?: (selectedRows: Row<TData>[]) => void
  onSelectionChange?: (selectedRows: Row<TData>[]) => void
  serverSidePagination?: ServerSidePaginationProps
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
}: DataTableProps<TData, TValue>) {
  const [tableState, setTableState] = useLocalStorage<Partial<TableState>>(
    `table-state:${tableId}`,
    {
      sorting: initialSorting,
      columnVisibility: initialColumnVisibility,
    }
  )

  // Initialize with empty state first to avoid triggering pagination reset
  const [columnFilters, setColumnFilters] = React.useState<ColumnFiltersState>(
    []
  )
  const [rowSelection, setRowSelection] = React.useState({})
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>(initialColumnVisibility ?? {})
  const [sorting, setSorting] = React.useState<SortingState>(initialSorting)

  // Track if component is mounted to prevent state updates on unmounted component
  const isMountedRef = React.useRef(false)
  const isInitialMountRef = React.useRef(true)

  React.useEffect(() => {
    isMountedRef.current = true

    // Restore state from localStorage after mount to avoid pagination reset during render
    if (tableState.columnFilters) {
      setColumnFilters(tableState.columnFilters)
    }
    if (tableState.rowSelection) {
      setRowSelection(tableState.rowSelection)
    }
    if (tableState.columnVisibility) {
      setColumnVisibility(tableState.columnVisibility)
    }
    if (tableState.sorting) {
      setSorting(tableState.sorting)
    }

    isInitialMountRef.current = false

    return () => {
      isMountedRef.current = false
    }
  }, [])

  React.useEffect(() => {
    // Skip the initial mount to prevent unnecessary localStorage updates
    if (isInitialMountRef.current) {
      return
    }

    // Only update if component is mounted and tableId exists
    if (tableId && isMountedRef.current) {
      // Use setTimeout to defer the state update to the next tick
      const timeoutId = setTimeout(() => {
        if (isMountedRef.current) {
          setTableState((prevState) => ({
            ...prevState,
            columnFilters,
            sorting,
            rowSelection,
            columnVisibility,
          }))
        }
      }, 0)

      return () => clearTimeout(timeoutId)
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
    autoResetPageIndex: false, // Prevent automatic page index reset
  })

  // Notify parent of selection changes
  React.useEffect(() => {
    // Skip if component is not mounted or during initial mount
    if (!isMountedRef.current || isInitialMountRef.current) {
      return
    }

    if (onSelectionChange) {
      // Defer the callback to avoid state updates during render
      const timeoutId = setTimeout(() => {
        if (isMountedRef.current) {
          const selectedRows = table.getFilteredSelectedRowModel().rows
          onSelectionChange(selectedRows)
        }
      }, 0)

      return () => clearTimeout(timeoutId)
    }
  }, [rowSelection, onSelectionChange, table])

  // Handle initial sync when data is first loaded
  const [hasData, setHasData] = React.useState(false)
  React.useEffect(() => {
    // Skip if component is not mounted
    if (!isMountedRef.current) {
      return
    }

    if (data && data.length > 0 && !hasData) {
      setHasData(true)
      if (onSelectionChange && Object.keys(rowSelection).length > 0) {
        // Defer the sync to avoid state updates during render
        const timeoutId = setTimeout(() => {
          if (isMountedRef.current) {
            const selectedRows = table.getFilteredSelectedRowModel().rows
            onSelectionChange(selectedRows)
          }
        }, 0)

        return () => clearTimeout(timeoutId)
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
                              "actions" && "text-right"
                          )}
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

              const content = flexRender(
                cell.column.columnDef.cell,
                cell.getContext()
              )

              // For action columns, don't wrap in Link
              if (isActionsCol || !href) {
                return (
                  <TableCell
                    key={cell.id}
                    className={cn(isActionsCol && "p-2")}
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
                <TableCell key={cell.id}>
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
