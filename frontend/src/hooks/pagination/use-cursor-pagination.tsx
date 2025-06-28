"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
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
}

export interface CursorPaginationState {
  currentCursor: string | null
  cursors: string[]
  currentPage: number
}

export function useCursorPagination<T, P extends CursorPaginationParams>({
  workspaceId,
  limit = 20,
  queryKey,
  queryFn,
  additionalParams,
  enabled = true,
}: UseCursorPaginationOptions<T, P>) {
  const [paginationState, setPaginationState] = useState<CursorPaginationState>(
    {
      currentCursor: null,
      cursors: [],
      currentPage: 0,
    }
  )

  // Reset pagination when limit changes
  useEffect(() => {
    setPaginationState({
      currentCursor: null,
      cursors: [],
      currentPage: 0,
    })
  }, [limit])

  const queryParams: P = {
    workspaceId,
    limit,
    cursor: paginationState.currentCursor || null,
    reverse: false,
    ...(additionalParams || {}),
  } as P

  const { data, isLoading, error, refetch } = useQuery<
    CursorPaginationResponse<T>,
    ApiError
  >({
    queryKey: [...queryKey, limit, paginationState.currentCursor],
    queryFn: () => queryFn(queryParams),
    enabled: enabled && !!workspaceId,
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
    setPaginationState({
      currentCursor: null,
      cursors: [],
      currentPage: 0,
    })
  }

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
