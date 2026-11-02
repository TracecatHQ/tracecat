"use client"

import { ArrowDownAZIcon, ArrowDownZAIcon } from "lucide-react"
import type { SortDirection } from "@/components/tables/table-row-query-context"
import type { SortingState } from "@/hooks/pagination/use-cursor-pagination"
import { cn } from "@/lib/utils"

/** The step after `current` in the asc -> desc -> none cycle. */
export function nextSortDirection(
  current: SortDirection | null
): SortDirection | false {
  if (current === null) return "asc"
  if (current === "asc") return "desc"
  return false
}

/** Props for {@link ColumnSortButton}. */
export interface ColumnSortButtonProps {
  columnName: string
  /** The grid's current sort, on this column or another. */
  sort: SortingState
  onSortChange: (columnName: string, direction: SortDirection | false) => void
  className?: string
}

/**
 * A column's name as a button that cycles its server-side sort: ascending,
 * descending, then none. The icon is muted until this column is the sorted one.
 */
export function ColumnSortButton({
  columnName,
  sort,
  onSortChange,
  className,
}: ColumnSortButtonProps) {
  const direction = sort.orderBy === columnName ? sort.sort : null

  let SortIcon = ArrowDownAZIcon
  let label = `Sort by ${columnName}`
  if (direction === "asc") {
    label = `${columnName}, sorted ascending. Activate to sort descending.`
  } else if (direction === "desc") {
    SortIcon = ArrowDownZAIcon
    label = `${columnName}, sorted descending. Activate to remove sorting.`
  }

  return (
    <button
      type="button"
      aria-label={label}
      data-sort={direction ?? "none"}
      onClick={() => onSortChange(columnName, nextSortDirection(direction))}
      className={cn(
        "flex min-w-0 items-center gap-1 rounded-sm text-xs font-medium",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
        className
      )}
    >
      <span className="truncate">{columnName}</span>
      <SortIcon
        className={cn(
          "size-3 shrink-0",
          direction ? "text-foreground" : "text-muted-foreground/50"
        )}
      />
    </button>
  )
}
