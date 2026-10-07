import type { CustomHeaderProps } from "ag-grid-react"
import { DatabaseZapIcon, TextSearchIcon } from "lucide-react"
import type { TableColumnRead } from "@/client"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { ColumnSortButton } from "@/components/tables/column-sort-button"
import { useTableRowQuery } from "@/components/tables/table-row-query-context"
import { useTableSearchContext } from "@/components/tables/table-search-context"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"
import type { SqlType } from "@/lib/data-type"

interface AgGridColumnHeaderParams extends CustomHeaderProps {
  tableColumn: TableColumnRead
}

/**
 * Render a table column header: its server-side sort button, type and index
 * indicators, and menu. The sort comes from the enclosing row query context.
 */
export function AgGridColumnHeader(params: AgGridColumnHeaderParams) {
  const { tableColumn, displayName } = params
  const search = useTableSearchContext()
  const query = useTableRowQuery()

  return (
    <div className="flex w-full items-center gap-2">
      {query ? (
        <ColumnSortButton
          columnName={tableColumn.name}
          sort={query.sort}
          onSortChange={query.onSortChange}
          className="shrink-0"
        />
      ) : (
        <span className="text-xs font-medium">{displayName}</span>
      )}
      <SqlTypeBadge type={tableColumn.type as SqlType} />
      {tableColumn.is_index && (
        <span className="inline-flex items-center rounded-full bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900 dark:text-green-100">
          <DatabaseZapIcon className="mr-1 size-3" />
          Index
        </span>
      )}
      {search?.configuration.data?.selected_column_ids?.includes(
        tableColumn.id
      ) && (
        <span className="inline-flex items-center rounded-full bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
          <TextSearchIcon className="mr-1 size-3" />
          Semantic
        </span>
      )}
      <TableViewColumnMenu column={tableColumn} />
    </div>
  )
}
