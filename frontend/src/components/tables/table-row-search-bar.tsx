"use client"

import { SearchIcon } from "lucide-react"
import type { TableColumnRead } from "@/client"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

/** Props for {@link TableRowSearchBar}. */
export interface TableRowSearchBarProps {
  /** The columns the API can search; the select's options. */
  searchableColumns: readonly TableColumnRead[]
  /** The term as typed. */
  term: string
  onTermChange: (term: string) => void
  /** Name of the searched column, or null when there is none to search. */
  column: string | null
  onColumnChange: (column: string) => void
  /** Row count to show on the right; omitted when no true count is known. */
  rowCount?: number
}

/**
 * The tables view's search row: a borderless input on the left, and on the
 * right the column being searched and the table's row count.
 */
export function TableRowSearchBar({
  searchableColumns,
  term,
  onTermChange,
  column,
  onColumnChange,
  rowCount,
}: TableRowSearchBarProps) {
  const canSearch = searchableColumns.length > 0

  return (
    <header className="flex h-10 shrink-0 items-center border-b pl-3 pr-4">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center">
          <SearchIcon className="size-4 text-muted-foreground" />
        </div>
        <Input
          type="text"
          aria-label="Search rows"
          placeholder={
            canSearch ? "Search rows..." : "No text columns to search"
          }
          value={term}
          maxLength={1000}
          onChange={(event) => onTermChange(event.target.value)}
          disabled={!canSearch}
          className={cn(
            "h-7 max-w-md rounded-sm border-none bg-transparent px-1.5 py-0",
            "text-sm",
            "shadow-none outline-none",
            "placeholder:text-muted-foreground",
            "focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring focus-visible:ring-offset-0"
          )}
        />
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-2">
        {canSearch && column && (
          <Select value={column} onValueChange={onColumnChange}>
            <SelectTrigger
              aria-label="Column to search"
              className="h-6 w-auto max-w-48 gap-1.5 px-2 text-xs font-medium shadow-none"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {searchableColumns.map((option) => (
                <SelectItem key={option.id} value={option.name}>
                  {option.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {rowCount !== undefined && rowCount > 0 && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {rowCount} {rowCount === 1 ? "row" : "rows"}
          </span>
        )}
      </div>
    </header>
  )
}
