import type { CustomHeaderProps } from "ag-grid-react"
import { DatabaseZapIcon } from "lucide-react"
import type { TableColumnRead } from "@/client"
import { SqlTypeBadge } from "@/components/data-type/sql-type-display"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"
import type { SqlType } from "@/lib/data-type"

interface AgGridColumnHeaderParams extends CustomHeaderProps {
  tableColumn: TableColumnRead
}

export function AgGridColumnHeader(params: AgGridColumnHeaderParams) {
  const { tableColumn, displayName } = params

  const handleSort = () => {
    // Cycle through: asc -> desc -> none
    const currentSort = params.column.getSort()
    if (!currentSort) {
      params.setSort("asc", false)
    } else if (currentSort === "asc") {
      params.setSort("desc", false)
    } else {
      params.setSort(null, false)
    }
  }

  const sortDirection = params.column.getSort()

  return (
    <div className="flex w-full items-center gap-2">
      <button
        type="button"
        onClick={handleSort}
        className="flex items-center gap-1 text-xs font-medium"
      >
        <span>{displayName}</span>
        {sortDirection === "asc" && (
          <span className="text-muted-foreground">&#9650;</span>
        )}
        {sortDirection === "desc" && (
          <span className="text-muted-foreground">&#9660;</span>
        )}
      </button>
      <SqlTypeBadge type={tableColumn.type as SqlType} />
      {tableColumn.is_index && (
        <span className="inline-flex items-center rounded-full bg-green-100 px-1.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900 dark:text-green-100">
          <DatabaseZapIcon className="mr-1 size-3" />
          Index
        </span>
      )}
      <TableViewColumnMenu column={tableColumn} />
    </div>
  )
}
