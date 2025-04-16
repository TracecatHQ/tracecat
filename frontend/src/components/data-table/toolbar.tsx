"use client"

import { useState } from "react"
import { Cross2Icon } from "@radix-ui/react-icons"
import { Row, Table } from "@tanstack/react-table"
import { Trash2Icon } from "lucide-react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DataTableViewOptions } from "@/components/data-table"
import { DataTableFacetedFilter } from "@/components/data-table/faceted-filter"
import { Spinner } from "@/components/loading/spinner"

export interface DataTableToolbarProps<TData> {
  filterProps?: DataTableToolbarFilterProps
  fields?: DataTableToolbarField[]
  onDeleteRows?: (selectedRows: Row<TData>[]) => void
}

interface DataTableToolbarFilterProps {
  placeholder: string
  column: string
}

interface DataTableToolbarField {
  column: string
  title?: string
  options: {
    label: string
    value: string
    icon?: React.ComponentType<{ className?: string }>
  }[]
}

interface InternalDataTableToolbarProps<TData>
  extends DataTableToolbarProps<TData> {
  table: Table<TData>
}

export function DataTableToolbar<TData>({
  filterProps,
  fields,
  table,
  onDeleteRows,
}: InternalDataTableToolbarProps<TData>) {
  const isFiltered = table.getState().columnFilters.length > 0
  const hasSelection = Object.keys(table.getState().rowSelection).length > 0
  const [isDeleting, setIsDeleting] = useState(false)
  return (
    <div className="flex items-center justify-between">
      <div className="flex flex-1 items-center space-x-2">
        <Input
          placeholder={filterProps?.placeholder ?? "Filter..."}
          value={
            table
              .getColumn(filterProps?.column ?? "")
              ?.getFilterValue() as string
          }
          onChange={(event) =>
            filterProps?.column
              ? table
                  .getColumn(filterProps.column)
                  ?.setFilterValue(event.target.value)
              : null
          }
          className="h-8 w-[150px] text-xs lg:w-[250px]"
        />
        {fields?.map((field) => (
          <DataTableFacetedFilter
            key={field.column}
            column={table.getColumn(field.column)}
            title={field.title}
            options={field.options}
          />
        ))}
        {isFiltered && (
          <Button
            variant="ghost"
            onClick={() => table.resetColumnFilters()}
            className="h-8 px-2 text-xs text-foreground/80 lg:px-3"
          >
            Reset
            <Cross2Icon className="ml-2 size-4" />
          </Button>
        )}
        {hasSelection && onDeleteRows && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-foreground/70 lg:px-3"
              >
                <span className="flex items-center">
                  <Trash2Icon className="size-4" />
                  <span className="ml-2">Delete</span>
                </span>
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Confirm Deletion</AlertDialogTitle>
                <AlertDialogDescription>
                  Are you sure you want to delete the selected items? This
                  action cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  onClick={() => {
                    try {
                      setIsDeleting(true)
                      const selectedRows =
                        table.getFilteredSelectedRowModel().rows
                      onDeleteRows(selectedRows)
                      table.resetRowSelection()
                    } finally {
                      setIsDeleting(false)
                    }
                  }}
                  disabled={isDeleting}
                >
                  {isDeleting ? (
                    <span className="flex items-center">
                      <Spinner className="size-4" />
                      <span className="ml-2">Deleting...</span>
                    </span>
                  ) : (
                    "Delete"
                  )}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
      <DataTableViewOptions table={table} />
    </div>
  )
}
