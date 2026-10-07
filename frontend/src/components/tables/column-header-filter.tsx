"use client"

import { ListFilterIcon, XIcon } from "lucide-react"
import { useState } from "react"
import { useTableRowQuery } from "@/components/tables/table-row-query-context"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { RowSearch } from "@/hooks/use-row-search"
import { cn } from "@/lib/utils"

/** Whether a filter would actually narrow the rows. */
function isActiveFilter(filter: RowSearch | undefined): filter is RowSearch {
  return Boolean(filter?.column) && filter?.term.trim() !== ""
}

/** Props for {@link ColumnHeaderFilter}. */
export interface ColumnHeaderFilterProps {
  columnName: string
  /** The grid's one column filter, on this column or another. */
  filter: RowSearch | undefined
  /** Replace the grid's filter, or pass null to clear it. */
  onFilterChange: (filter: RowSearch | null) => void
}

/**
 * A column header's filter button. It opens a popover with a text input that
 * filters the grid's rows by this column; typing here replaces a filter held
 * by any other column. The button is highlighted while this column filters.
 */
export function ColumnHeaderFilter({
  columnName,
  filter,
  onFilterChange,
}: ColumnHeaderFilterProps) {
  const [open, setOpen] = useState(false)
  const term = filter?.column === columnName ? filter.term : ""
  const isActive = term.trim() !== ""

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={
            isActive ? `Filter ${columnName}, active` : `Filter ${columnName}`
          }
          data-active={isActive}
          className={cn(
            "flex size-5 shrink-0 items-center justify-center rounded-sm",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
            isActive
              ? "bg-primary/10 text-primary"
              : "text-muted-foreground/50 hover:text-foreground"
          )}
        >
          <ListFilterIcon className="size-3" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        portal
        align="start"
        className="w-64 p-2"
        // React events bubble through the portal to the grid, whose own key
        // handling (clipboard, header navigation) must not see this typing.
        onKeyDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-1">
          <Input
            autoFocus
            type="text"
            value={term}
            aria-label={`Search ${columnName}`}
            placeholder={`Search ${columnName}...`}
            onChange={(event) =>
              onFilterChange({ column: columnName, term: event.target.value })
            }
            className="h-7"
          />
          <Button
            variant="ghost"
            size="sm"
            aria-label="Clear filter"
            className="size-7 shrink-0 p-0 text-muted-foreground"
            disabled={term === ""}
            onClick={() => onFilterChange(null)}
          >
            <XIcon className="size-3.5" />
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/**
 * The grid's "no rows" overlay for grids with header filters. When a filter is
 * what emptied the grid it says so and offers to clear it.
 */
export function FilteredRowsEmptyOverlay() {
  const query = useTableRowQuery()
  const filter = query?.filter
  const onFilterChange = query?.onFilterChange
  if (!onFilterChange || !isActiveFilter(filter)) {
    return <span className="text-sm text-muted-foreground">No rows</span>
  }
  return (
    // The grid's overlay ignores the pointer; the button must not.
    <div className="pointer-events-auto flex flex-col items-center gap-2 px-4 text-center">
      <span className="text-sm text-muted-foreground">
        No rows match &ldquo;{filter.term.trim()}&rdquo; in {filter.column}
      </span>
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-xs"
        onClick={() => onFilterChange(null)}
      >
        Clear filter
      </Button>
    </div>
  )
}
