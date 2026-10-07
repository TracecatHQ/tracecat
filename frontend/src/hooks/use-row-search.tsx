"use client"

import { useCallback, useEffect, useState } from "react"
import type { ApiError } from "@/client"
import { useDebounce } from "@/hooks/use-debounce"

/** How long typing must pause before a row search is sent. */
export const ROW_SEARCH_DEBOUNCE_MS = 300

/** A text search over one column of a table's rows. */
export interface RowSearch {
  /** Name of the searched column, or null when none is picked yet. */
  column: string | null
  /** The term as typed, untrimmed. */
  term: string
}

const EMPTY_ROW_SEARCH: RowSearch = { column: null, term: "" }

/** The search params a rows request takes; both null when not searching. */
export interface RowSearchParams {
  searchTerm: string | null
  searchColumn: string | null
}

/**
 * Turn a search into request params: the term is trimmed, and nothing is sent
 * without both a term and a column.
 */
export function toRowSearchParams(search: RowSearch): RowSearchParams {
  const term = search.term.trim()
  if (!term || !search.column) {
    return { searchTerm: null, searchColumn: null }
  }
  return { searchTerm: term, searchColumn: search.column }
}

/**
 * State for a single-column row search. `search` follows the input as typed;
 * `debouncedSearch` trails it and is the one to send. `clearSearch` resets
 * both at once, without waiting out the debounce.
 */
export function useRowSearch() {
  const [search, setSearch] = useState<RowSearch>(EMPTY_ROW_SEARCH)
  const [debouncedSearch, setDebouncedSearch] = useDebounce(
    search,
    ROW_SEARCH_DEBOUNCE_MS
  )

  const clearSearch = useCallback(() => {
    setSearch(EMPTY_ROW_SEARCH)
    setDebouncedSearch(EMPTY_ROW_SEARCH)
  }, [setDebouncedSearch])

  return { search, setSearch, debouncedSearch, clearSearch }
}

/**
 * Whether a rows request failed because its sort or search column is gone or
 * no longer searchable, going by the API's 400 detail. Other 400s, such as an
 * over-long search term, are not stale columns.
 */
export function isStaleColumnError(error: ApiError | null): boolean {
  if (error?.status !== 400) return false
  const detail = (error.body as { detail?: unknown } | null)?.detail
  if (typeof detail !== "string") return false
  return (
    detail.startsWith("Invalid search_column") ||
    detail.startsWith("Invalid order_by column") ||
    detail.endsWith("does not support text search")
  )
}

/**
 * Clears a sort or search whose column was deleted or renamed: `reset` is
 * called to drop both rather than leave the grid on an error. Returns whether
 * the current error is such a rejection, so the caller can skip rendering it.
 */
export function useStaleRowQueryReset({
  error,
  isActive,
  reset,
}: {
  error: ApiError | null
  /** Whether the failed request carried a sort or a search. */
  isActive: boolean
  reset: () => void
}): boolean {
  const isStale = isActive && isStaleColumnError(error)
  useEffect(() => {
    if (isStale) reset()
  }, [isStale, reset])
  return isStale
}
