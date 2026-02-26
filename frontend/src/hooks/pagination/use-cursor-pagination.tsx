"use client"

import { useQuery } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import type { ApiError } from "@/client"

export interface CursorPaginationResponse<T> {
  items: T[]
  next_cursor?: string | null
  prev_cursor?: string | null
  has_more?: boolean
  has_previous?: boolean
  total_estimate?: number | null
}

export interface CursorPaginationParams {
  workspaceId: string
  limit?: number
  cursor?: string | null
  reverse?: boolean
  orderBy?: string | null
  sort?: "asc" | "desc" | null
}

export interface SortingState {
  orderBy: string | null
  sort: "asc" | "desc" | null
}

export interface UseCursorPaginationOptions<
  T,
  P extends CursorPaginationParams,
> {
  workspaceId: string
  limit?: number
  queryKey: (string | number | null)[]
  queryFn: (params: P) => Promise<CursorPaginationResponse<T>>
  additionalParams?: Omit<P, keyof CursorPaginationParams>
  enabled?: boolean
  staleTime?: number
  refetchOnWindowFocus?: boolean
}

export interface CursorPaginationState {
  currentCursor: string | null
  cursors: string[]
  currentPage: number
}

const DEFAULT_PAGINATION_STATE: CursorPaginationState = {
  currentCursor: null,
  cursors: [],
  currentPage: 0,
}

export function useCursorPagination<T, P extends CursorPaginationParams>({
  workspaceId,
  limit = 20,
  queryKey,
  queryFn,
  additionalParams,
  enabled = true,
  staleTime,
  refetchOnWindowFocus,
}: UseCursorPaginationOptions<T, P>) {
  const [paginationState, setPaginationState] = useState<CursorPaginationState>(
    DEFAULT_PAGINATION_STATE
  )

  const [sortingState, setSortingState] = useState<SortingState>({
    orderBy: null,
    sort: null,
  })

  const queryKeyFingerprint = JSON.stringify(queryKey)
  const nextResetFingerprint = `${queryKeyFingerprint}|${limit}|${sortingState.orderBy ?? ""}|${sortingState.sort ?? ""}`
  const [resetFingerprint, setResetFingerprint] = useState(nextResetFingerprint)

  // Ensure cursor state is reset in the same render that changes query inputs.
  if (resetFingerprint !== nextResetFingerprint) {
    setResetFingerprint(nextResetFingerprint)
    setPaginationState(DEFAULT_PAGINATION_STATE)
  }

  const queryParams: P = {
    workspaceId,
    limit,
    cursor: paginationState.currentCursor || null,
    reverse: false,
    orderBy: sortingState.orderBy,
    sort: sortingState.sort,
    ...(additionalParams || {}),
  } as P

  const { data, isLoading, error, refetch } = useQuery<
    CursorPaginationResponse<T>,
    ApiError
  >({
    queryKey: [
      ...queryKey,
      limit,
      paginationState.currentCursor,
      sortingState.orderBy,
      sortingState.sort,
    ],
    queryFn: () => queryFn(queryParams),
    enabled: enabled && !!workspaceId,
    staleTime,
    refetchOnWindowFocus,
  })

  const goToNextPage = () => {
    if (!data?.next_cursor) return

    setPaginationState((prev) => ({
      currentCursor: data.next_cursor || null,
      cursors: [...prev.cursors, prev.currentCursor].filter(
        Boolean
      ) as string[],
      currentPage: prev.currentPage + 1,
    }))
  }

  const goToPreviousPage = () => {
    if (paginationState.currentPage === 0) return

    const newCursors = [...paginationState.cursors]
    const previousCursor = newCursors.pop() || null

    setPaginationState({
      currentCursor: previousCursor,
      cursors: newCursors,
      currentPage: paginationState.currentPage - 1,
    })
  }

  const goToFirstPage = () => {
    setPaginationState(DEFAULT_PAGINATION_STATE)
  }

  // Sorting control for server-side sorting
  const setSorting = useCallback(
    (columnId: string, direction: "asc" | "desc" | false) => {
      if (direction === false) {
        setSortingState({ orderBy: null, sort: null })
      } else {
        setSortingState({ orderBy: columnId, sort: direction })
      }
    },
    []
  )

  return {
    // Data
    data: data?.items || [],
    isLoading,
    error,
    refetch,

    // Pagination controls
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,

    // Sorting controls
    setSorting,
    sortingState,

    // Pagination state
    hasNextPage: data?.has_more || false,
    hasPreviousPage: paginationState.currentPage > 0,
    currentPage: paginationState.currentPage,
    pageSize: limit,

    // For UI display
    totalItems: data?.items?.length || 0,
    startItem: paginationState.currentPage * limit + 1,
    endItem: paginationState.currentPage * limit + (data?.items?.length || 0),

    // Use backend's total estimate from PostgreSQL table statistics
    totalEstimate: data?.total_estimate || 0,
    totalPages: data?.total_estimate
      ? Math.ceil(data.total_estimate / limit)
      : data?.has_more
        ? paginationState.currentPage + 2
        : paginationState.currentPage + 1,
  }
}
