"use client"

import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { useCallback, useMemo, useState } from "react"
import type { DateRange } from "react-day-picker"
import {
  type CasePriority,
  type CaseReadMinimal,
  type CaseSearchAggregateRead,
  type CaseSeverity,
  type CaseStatus,
  type CasesSearchCaseAggregatesResponse,
  type CasesSearchCasesResponse,
  casesSearchCaseAggregates,
  casesSearchCases,
} from "@/client"
import type { FilterMode, SortDirection } from "@/components/cases/cases-header"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

const CASES_PAGE_SIZE = 100
const CASES_REFETCH_INTERVAL_MS = 120000
const ALL_CASE_STATUSES: ReadonlyArray<CaseStatus> = [
  "unknown",
  "new",
  "in_progress",
  "on_hold",
  "resolved",
  "closed",
  "other",
]
const ALL_CASE_PRIORITIES: ReadonlyArray<CasePriority> = [
  "unknown",
  "low",
  "medium",
  "high",
  "critical",
  "other",
]
const ALL_CASE_SEVERITIES: ReadonlyArray<CaseSeverity> = [
  "unknown",
  "informational",
  "low",
  "medium",
  "high",
  "critical",
  "fatal",
  "other",
]
const EMPTY_STAGE_COUNTS: CaseSearchAggregateRead["status_groups"] = {
  new: 0,
  in_progress: 0,
  on_hold: 0,
  resolved: 0,
  other: 0,
}

// Preset date filter values (relative time periods)
export type CaseDatePreset = "1d" | "3d" | "1w" | "1m" | null

// Date filter can be either a preset or a custom date range
export type CaseDateFilterValue =
  | { type: "preset"; value: CaseDatePreset }
  | { type: "range"; value: DateRange }

export interface DropdownFilterState {
  values: string[]
  mode: FilterMode
  sortDirection: SortDirection
}

export interface UseCasesFilters {
  searchQuery: string
  statusFilter: CaseStatus[]
  statusMode: FilterMode
  priorityFilter: CasePriority[]
  priorityMode: FilterMode
  prioritySortDirection: SortDirection
  severityFilter: CaseSeverity[]
  severityMode: FilterMode
  severitySortDirection: SortDirection
  assigneeFilter: string[]
  assigneeMode: FilterMode
  assigneeSortDirection: SortDirection
  tagFilter: string[]
  tagMode: FilterMode
  tagSortDirection: SortDirection
  dropdownFilters: Record<string, DropdownFilterState>
  updatedAfter: CaseDateFilterValue
  createdAfter: CaseDateFilterValue
}

export interface UseCasesOptions {
  enabled?: boolean
  autoRefresh?: boolean
}

export interface UseCasesResult {
  cases: CaseReadMinimal[]
  isLoading: boolean
  error: Error | null
  refetch: () => void
  filters: UseCasesFilters
  setSearchQuery: (query: string) => void
  setStatusFilter: (status: CaseStatus[]) => void
  setStatusMode: (mode: FilterMode) => void
  setPriorityFilter: (priority: CasePriority[]) => void
  setPriorityMode: (mode: FilterMode) => void
  setPrioritySortDirection: (direction: SortDirection) => void
  setSeverityFilter: (severity: CaseSeverity[]) => void
  setSeverityMode: (mode: FilterMode) => void
  setSeveritySortDirection: (direction: SortDirection) => void
  setAssigneeFilter: (assignee: string[]) => void
  setAssigneeMode: (mode: FilterMode) => void
  setAssigneeSortDirection: (direction: SortDirection) => void
  setTagFilter: (tags: string[]) => void
  setTagMode: (mode: FilterMode) => void
  setTagSortDirection: (direction: SortDirection) => void
  setDropdownFilter: (ref: string, values: string[]) => void
  setDropdownMode: (ref: string, mode: FilterMode) => void
  setDropdownSortDirection: (ref: string, direction: SortDirection) => void
  setUpdatedAfter: (value: CaseDateFilterValue) => void
  setCreatedAfter: (value: CaseDateFilterValue) => void
  totalFilteredCaseEstimate: number | null
  stageCounts: CaseSearchAggregateRead["status_groups"] | null
  isCountsLoading: boolean
  isCountsFetching: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => void
}

// Helper to get Date from preset filter value
function getDateFromPreset(preset: CaseDatePreset): Date | null {
  if (!preset) return null
  const now = new Date()
  switch (preset) {
    case "1d":
      return new Date(now.getTime() - 24 * 60 * 60 * 1000)
    case "3d":
      return new Date(now.getTime() - 3 * 24 * 60 * 60 * 1000)
    case "1w":
      return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
    case "1m":
      return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)
    default:
      return null
  }
}

// Helper to get start/end dates from a filter value
function getDateBoundsFromFilter(filter: CaseDateFilterValue): {
  start: Date | null
  end: Date | null
} {
  if (filter.type === "preset") {
    return { start: getDateFromPreset(filter.value), end: null }
  }
  return {
    start: filter.value.from ?? null,
    end: filter.value.to ?? null,
  }
}

function toEndOfDay(date: Date): Date {
  const end = new Date(date)
  end.setHours(23, 59, 59, 999)
  return end
}

// Default filter value (no filter)
export const DEFAULT_DATE_FILTER: CaseDateFilterValue = {
  type: "preset",
  value: null,
}

export const DEFAULT_CREATED_PRESET: CaseDatePreset = "1m"

export const DEFAULT_CREATED_FILTER: CaseDateFilterValue = {
  type: "preset",
  value: DEFAULT_CREATED_PRESET,
}

function resolveEnumIncludeFilter<T extends string>(
  selected: T[],
  mode: FilterMode,
  universe: ReadonlyArray<T>
): { values: T[] | undefined; matchesNone: boolean } {
  if (selected.length === 0) {
    return { values: undefined, matchesNone: false }
  }
  if (mode === "include") {
    return { values: selected, matchesNone: false }
  }

  const selectedSet = new Set(selected)
  const complement = universe.filter((value) => !selectedSet.has(value))
  if (complement.length === 0) {
    return { values: undefined, matchesNone: true }
  }
  return { values: complement, matchesNone: false }
}

export function useCases(options: UseCasesOptions = {}): UseCasesResult {
  const { enabled = true, autoRefresh = true } = options
  const workspaceId = useWorkspaceId()

  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState<CaseStatus[]>([])
  const [statusMode, setStatusMode] = useState<FilterMode>("include")
  const [priorityFilter, setPriorityFilter] = useState<CasePriority[]>([])
  const [priorityMode, setPriorityMode] = useState<FilterMode>("include")
  const [prioritySortDirection, setPrioritySortDirection] =
    useState<SortDirection>(null)
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity[]>([])
  const [severityMode, setSeverityMode] = useState<FilterMode>("include")
  const [severitySortDirection, setSeveritySortDirection] =
    useState<SortDirection>(null)
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>([])
  const [assigneeMode, setAssigneeModeState] = useState<FilterMode>("include")
  const [assigneeSortDirection, setAssigneeSortDirection] =
    useState<SortDirection>(null)
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [tagMode, setTagModeState] = useState<FilterMode>("include")
  const [tagSortDirection, setTagSortDirection] = useState<SortDirection>(null)
  const [dropdownFilters, setDropdownFilters] = useState<
    Record<string, DropdownFilterState>
  >({})
  const [updatedAfter, setUpdatedAfter] =
    useState<CaseDateFilterValue>(DEFAULT_DATE_FILTER)
  const [createdAfter, setCreatedAfter] = useState<CaseDateFilterValue>(
    DEFAULT_CREATED_FILTER
  )

  const setAssigneeMode = useCallback((_mode: FilterMode) => {
    setAssigneeModeState("include")
  }, [])

  const setTagMode = useCallback((_mode: FilterMode) => {
    setTagModeState("include")
  }, [])

  const setDropdownFilter = useCallback(
    (ref: string, values: string[]) =>
      setDropdownFilters((prev) => ({
        ...prev,
        [ref]: {
          ...prev[ref],
          values,
          mode: "include",
          sortDirection: prev[ref]?.sortDirection ?? null,
        },
      })),
    []
  )

  const setDropdownMode = useCallback(
    (ref: string, _mode: FilterMode) =>
      setDropdownFilters((prev) => ({
        ...prev,
        [ref]: {
          ...prev[ref],
          values: prev[ref]?.values ?? [],
          mode: "include",
          sortDirection: prev[ref]?.sortDirection ?? null,
        },
      })),
    []
  )

  const setDropdownSortDirection = useCallback(
    (ref: string, direction: SortDirection) =>
      setDropdownFilters((prev) => ({
        ...prev,
        [ref]: {
          ...prev[ref],
          values: prev[ref]?.values ?? [],
          mode: "include",
          sortDirection: direction,
        },
      })),
    []
  )

  const { apiQueryParams, hasImpossibleEnumFilter } = useMemo(() => {
    const normalizedSearch = searchQuery.trim()
    const updatedBounds = getDateBoundsFromFilter(updatedAfter)
    const createdBounds = getDateBoundsFromFilter(createdAfter)
    const resolvedStatus = resolveEnumIncludeFilter(
      statusFilter,
      statusMode,
      ALL_CASE_STATUSES
    )
    const resolvedPriority = resolveEnumIncludeFilter(
      priorityFilter,
      priorityMode,
      ALL_CASE_PRIORITIES
    )
    const resolvedSeverity = resolveEnumIncludeFilter(
      severityFilter,
      severityMode,
      ALL_CASE_SEVERITIES
    )

    const dropdownIncludes: string[] = []
    for (const [definitionRef, state] of Object.entries(dropdownFilters)) {
      if (state.mode !== "include" || state.values.length === 0) {
        continue
      }
      for (const optionRef of state.values) {
        dropdownIncludes.push(`${definitionRef}:${optionRef}`)
      }
    }

    return {
      apiQueryParams: {
        searchTerm: normalizedSearch.length > 0 ? normalizedSearch : undefined,
        status: resolvedStatus.values,
        priority: resolvedPriority.values,
        severity: resolvedSeverity.values,
        assigneeId:
          assigneeFilter.length > 0 && assigneeMode === "include"
            ? assigneeFilter
            : undefined,
        tags:
          tagFilter.length > 0 && tagMode === "include" ? tagFilter : undefined,
        dropdown: dropdownIncludes.length > 0 ? dropdownIncludes : undefined,
        startTime: createdBounds.start?.toISOString(),
        endTime: createdBounds.end
          ? toEndOfDay(createdBounds.end).toISOString()
          : undefined,
        updatedAfter: updatedBounds.start?.toISOString(),
        updatedBefore: updatedBounds.end
          ? toEndOfDay(updatedBounds.end).toISOString()
          : undefined,
      },
      hasImpossibleEnumFilter:
        resolvedStatus.matchesNone ||
        resolvedPriority.matchesNone ||
        resolvedSeverity.matchesNone,
    }
  }, [
    searchQuery,
    statusFilter,
    statusMode,
    priorityFilter,
    priorityMode,
    severityFilter,
    severityMode,
    assigneeFilter,
    assigneeMode,
    tagFilter,
    tagMode,
    dropdownFilters,
    updatedAfter,
    createdAfter,
  ])

  const serverSortParams = useMemo(() => {
    if (prioritySortDirection) {
      return { orderBy: "priority" as const, sort: prioritySortDirection }
    }
    if (severitySortDirection) {
      return { orderBy: "severity" as const, sort: severitySortDirection }
    }
    return { orderBy: "created_at" as const, sort: "desc" as const }
  }, [prioritySortDirection, severitySortDirection])

  const filtersKey = useMemo(
    () => JSON.stringify(apiQueryParams),
    [apiQueryParams]
  )

  const rowsKey = useMemo(
    () =>
      JSON.stringify({
        filtersKey,
        orderBy: serverSortParams.orderBy,
        sort: serverSortParams.sort,
      }),
    [filtersKey, serverSortParams.orderBy, serverSortParams.sort]
  )

  const computeRefetchInterval = useCallback(() => {
    if (!autoRefresh) {
      return false
    }

    if (
      typeof document !== "undefined" &&
      document.visibilityState === "hidden"
    ) {
      return false
    }

    return CASES_REFETCH_INTERVAL_MS
  }, [autoRefresh])

  const {
    data: rowsData,
    isLoading,
    error,
    refetch: refetchRows,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useInfiniteQuery<CasesSearchCasesResponse, TracecatApiError>({
    queryKey: ["cases", workspaceId, rowsKey],
    queryFn: ({ pageParam }) =>
      casesSearchCases({
        workspaceId,
        ...apiQueryParams,
        orderBy: serverSortParams.orderBy,
        sort: serverSortParams.sort,
        limit: CASES_PAGE_SIZE,
        cursor: (pageParam as string | null) ?? undefined,
      }),
    enabled: enabled && Boolean(workspaceId) && !hasImpossibleEnumFilter,
    initialPageParam: null,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || !lastPage.next_cursor) {
        return undefined
      }
      return lastPage.next_cursor
    },
    retry: retryHandler,
    refetchInterval: computeRefetchInterval(),
    refetchOnWindowFocus: false,
    staleTime: 60000,
  })

  const {
    data: aggregateData,
    isLoading: isCountsLoading,
    isFetching: isCountsFetching,
    refetch: refetchCounts,
  } = useQuery<CasesSearchCaseAggregatesResponse, TracecatApiError>({
    queryKey: ["cases", "aggregate", workspaceId, filtersKey],
    queryFn: () =>
      casesSearchCaseAggregates({
        workspaceId,
        ...apiQueryParams,
      }),
    enabled: enabled && Boolean(workspaceId) && !hasImpossibleEnumFilter,
    retry: retryHandler,
    refetchInterval: computeRefetchInterval(),
    refetchOnWindowFocus: false,
    staleTime: 60000,
  })

  const flattenedCases = useMemo(() => {
    const pages = rowsData?.pages ?? []
    const deduped: CaseReadMinimal[] = []
    const seenIds = new Set<string>()

    for (const page of pages) {
      for (const item of page.items) {
        if (seenIds.has(item.id)) {
          continue
        }
        seenIds.add(item.id)
        deduped.push(item)
      }
    }

    return deduped
  }, [rowsData?.pages])

  const sortedCases = useMemo(() => {
    if (assigneeSortDirection) {
      const multiplier = assigneeSortDirection === "asc" ? 1 : -1
      return [...flattenedCases].sort((a, b) => {
        const aName = a.assignee?.email ?? ""
        const bName = b.assignee?.email ?? ""
        return multiplier * aName.localeCompare(bName)
      })
    }

    if (tagSortDirection) {
      const multiplier = tagSortDirection === "asc" ? 1 : -1
      return [...flattenedCases].sort((a, b) => {
        const aTag = a.tags?.[0]?.name ?? ""
        const bTag = b.tags?.[0]?.name ?? ""
        if (!aTag && bTag) return 1
        if (aTag && !bTag) return -1
        return multiplier * aTag.localeCompare(bTag)
      })
    }

    return flattenedCases
  }, [flattenedCases, assigneeSortDirection, tagSortDirection])

  const totalFilteredCaseEstimate = hasImpossibleEnumFilter
    ? 0
    : (aggregateData?.total ?? rowsData?.pages[0]?.total_estimate ?? null)

  const refetch = useCallback(() => {
    void refetchRows()
    void refetchCounts()
  }, [refetchRows, refetchCounts])

  const handleFetchNextPage = useCallback(() => {
    if (!hasNextPage || isFetchingNextPage) {
      return
    }
    void fetchNextPage()
  }, [fetchNextPage, hasNextPage, isFetchingNextPage])

  return {
    cases: sortedCases,
    isLoading,
    error: error ?? null,
    refetch,
    filters: {
      searchQuery,
      statusFilter,
      statusMode,
      priorityFilter,
      priorityMode,
      prioritySortDirection,
      severityFilter,
      severityMode,
      severitySortDirection,
      assigneeFilter,
      assigneeMode,
      assigneeSortDirection,
      tagFilter,
      tagMode,
      tagSortDirection,
      dropdownFilters,
      updatedAfter,
      createdAfter,
    },
    setSearchQuery,
    setStatusFilter,
    setStatusMode,
    setPriorityFilter,
    setPriorityMode,
    setPrioritySortDirection,
    setSeverityFilter,
    setSeverityMode,
    setSeveritySortDirection,
    setAssigneeFilter,
    setAssigneeMode,
    setAssigneeSortDirection,
    setTagFilter,
    setTagMode,
    setTagSortDirection,
    setDropdownFilter,
    setDropdownMode,
    setDropdownSortDirection,
    setUpdatedAfter,
    setCreatedAfter,
    totalFilteredCaseEstimate,
    stageCounts: hasImpossibleEnumFilter
      ? EMPTY_STAGE_COUNTS
      : (aggregateData?.status_groups ?? null),
    isCountsLoading: hasImpossibleEnumFilter ? false : isCountsLoading,
    isCountsFetching: hasImpossibleEnumFilter ? false : isCountsFetching,
    hasNextPage: hasImpossibleEnumFilter ? false : Boolean(hasNextPage),
    isFetchingNextPage,
    fetchNextPage: handleFetchNextPage,
  }
}
