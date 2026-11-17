"use client"

import type { CustomHeaderProps } from "ag-grid-react"
import type { TableColumnRead } from "@/client"
import { isSearchableColumn } from "@/components/tables/ag-grid-column-defs"
import { ColumnHeaderFilter } from "@/components/tables/column-header-filter"
import { ColumnSortButton } from "@/components/tables/column-sort-button"
import { useTableRowQuery } from "@/components/tables/table-row-query-context"

interface TableRowsGridHeaderParams extends CustomHeaderProps {
  tableColumn: TableColumnRead
}

/**
 * Column header for a `TableRowsGrid` with a row query: the column's sort
 * button, plus a filter button on columns the API can search.
 */
export function TableRowsGridHeader({
  tableColumn,
  displayName,
}: TableRowsGridHeaderParams) {
  const query = useTableRowQuery()
  if (!query) {
    return <span className="truncate text-xs font-medium">{displayName}</span>
  }
  const { onFilterChange } = query

  return (
    <div className="flex w-full min-w-0 items-center justify-between gap-1">
      <ColumnSortButton
        columnName={tableColumn.name}
        sort={query.sort}
        onSortChange={query.onSortChange}
      />
      {onFilterChange && isSearchableColumn(tableColumn) && (
        <ColumnHeaderFilter
          columnName={tableColumn.name}
          filter={query.filter}
          onFilterChange={onFilterChange}
        />
      )}
    </div>
  )
}
