"use client"

import { createContext, useContext } from "react"
import type { SortingState } from "@/hooks/pagination/use-cursor-pagination"
import type { RowSearch } from "@/hooks/use-row-search"

/** Direction of a server-side column sort. */
export type SortDirection = "asc" | "desc"

/**
 * The server-side sort and column filter of a rows grid, as its column headers
 * see them. Headers are mounted once by the grid, so they read this from
 * context to stay current instead of from their params.
 */
export interface TableRowQuery {
  /** The sort being requested; at most one column. */
  sort: SortingState
  /** Sort by a column, or pass `false` to drop the sort. */
  onSortChange: (columnName: string, direction: SortDirection | false) => void
  /** The column filter as typed, before debouncing. */
  filter?: RowSearch
  /**
   * The column filter the rows on screen were fetched with: debounced, and
   * trailing `filter` until its request has landed.
   */
  appliedFilter?: RowSearch
  /**
   * Replace the column filter, or pass null to clear it. Headers only offer
   * filtering when this is set.
   */
  onFilterChange?: (filter: RowSearch | null) => void
}

/** Carries a grid's {@link TableRowQuery} to its header components. */
export const TableRowQueryContext = createContext<TableRowQuery | null>(null)

/** The enclosing grid's row query, or null when it has none. */
export function useTableRowQuery(): TableRowQuery | null {
  return useContext(TableRowQueryContext)
}
