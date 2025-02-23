"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import { Table } from "@tanstack/react-table"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DataTableViewOptions } from "@/components/data-table"
import { DataTableFacetedFilter } from "@/components/data-table/faceted-filter"

export interface DataTableToolbarProps {
  filterProps?: DataTableToolbarFilterProps
  fields?: DataTableToolbarField[]
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

interface InternalDataTableToolbarProps<TData> extends DataTableToolbarProps {
  table: Table<TData>
}

export function DataTableToolbar<TData>({
  filterProps,
  fields,
  table,
}: InternalDataTableToolbarProps<TData>) {
  const isFiltered = table.getState().columnFilters.length > 0

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
      </div>
      <DataTableViewOptions table={table} />
    </div>
  )
}
