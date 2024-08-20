"use client"

import * as React from "react"
import {
  Column,
  ColumnDef,
  ColumnFiltersState,
  flexRender,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  Row,
  SortingState,
  useReactTable,
  VisibilityState,
} from "@tanstack/react-table"
import { AlertTriangleIcon } from "lucide-react"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import AuxClickMenu, {
  AuxClickMenuOptionProps,
} from "@/components/aux-click-menu"
import { CenteredSpinner } from "@/components/loading/spinner"
import { DataTablePagination, DataTableToolbar } from "@/components/table"

import { DataTableToolbarProps } from "./toolbar"

export type TableCol<TData> = {
  table: ReturnType<typeof useReactTable<TData>>
  column: Column<TData>
}
interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[]
  data?: TData[]
  onClickRow?: (row: Row<TData>) => () => void
  toolbarProps?: DataTableToolbarProps
  tableHeaderAuxOptions?: AuxClickMenuOptionProps<TableCol<TData>>[]
  isLoading?: boolean
  error?: Error
  emptyMessage?: string
  errorMessage?: string
  showSelectedRows?: boolean
}

export function DataTable<TData, TValue>({
  columns,
  data,
  onClickRow,
  toolbarProps,
  tableHeaderAuxOptions,
  isLoading,
  error,
  emptyMessage,
  errorMessage,
  showSelectedRows = false,
}: DataTableProps<TData, TValue>) {
  const [rowSelection, setRowSelection] = React.useState({})
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({})
  const [columnFilters, setColumnFilters] = React.useState<ColumnFiltersState>(
    []
  )
  const [sorting, setSorting] = React.useState<SortingState>([])

  const table = useReactTable({
    data: data || [],
    columns,
    state: {
      sorting,
      columnVisibility,
      rowSelection,
      columnFilters,
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
  })

  return (
    <div>
      <div className="space-y-4">
        {toolbarProps && <DataTableToolbar table={table} {...toolbarProps} />}
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
                        <TableHead colSpan={header.colSpan}>
                          {header.isPlaceholder
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
                emptyMessage={emptyMessage}
                errorMessage={errorMessage}
              />
            </TableBody>
          </Table>
        </div>
        <DataTablePagination
          table={table}
          showSelectedRows={showSelectedRows}
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
  emptyMessage = "No results.",
  errorMessage = "Failed to fetch data",
}: {
  isLoading?: boolean
  error?: Error
  table: ReturnType<typeof useReactTable<TData>>
  colSpan: number
  onClickRow?: (row: Row<TData>) => () => void
  emptyMessage?: string
  errorMessage?: string
}) {
  if (isLoading) {
    return (
      <TableRow>
        <TableCell
          colSpan={colSpan}
          className="font-sm h-24 text-center text-xs text-muted-foreground"
        >
          <CenteredSpinner />
        </TableCell>
      </TableRow>
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
      {table.getRowModel().rows.map((row) => (
        <TableRow
          key={row.id}
          data-state={row.getIsSelected() && "selected"}
          onClick={onClickRow?.(row)}
          className="cursor-pointer"
        >
          {row.getVisibleCells().map((cell) => (
            <TableCell key={cell.id}>
              {flexRender(cell.column.columnDef.cell, cell.getContext())}
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  )
}
