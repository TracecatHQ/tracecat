"use client"

import { useQuery } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { DateRange } from "react-day-picker"
import {
  type CasePriority,
  type CaseReadMinimal,
  type CaseSeverity,
  type CaseStatus,
  type CasesListCasesResponse,
  casesListCases,
} from "@/client"
import type { FilterMode, SortDirection } from "@/components/cases/cases-header"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

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

export type CasesRecencySort = "asc" | "desc"

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
  updatedAtSort: CasesRecencySort
  limit: number
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
  setUpdatedAtSort: (value: CasesRecencySort) => void
  setLimit: (limit: number) => void
  goToNextPage: () => void
  goToPreviousPage: () => void
  hasNextPage: boolean
  hasPreviousPage: boolean
  currentPage: number
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
  // Custom range
  return {
    start: filter.value.from ?? null,
    end: filter.value.to ?? null,
  }
}

// Default filter value (no filter)
export const DEFAULT_DATE_FILTER: CaseDateFilterValue = {
  type: "preset",
  value: null,
}

// Priority order mapping (higher value = higher priority)
const PRIORITY_ORDER: Record<CasePriority, number> = {
  unknown: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
  other: 0,
}

// Severity order mapping (higher value = higher severity)
const SEVERITY_ORDER: Record<CaseSeverity, number> = {
  unknown: 0,
  informational: 1,
  low: 2,
  medium: 3,
  high: 4,
  critical: 5,
  fatal: 6,
  other: 0,
}

export function useCases(options: UseCasesOptions = {}): UseCasesResult {
  const { enabled = true, autoRefresh = true } = options
  const workspaceId = useWorkspaceId()

  // Filter state - multi-select with include/exclude modes
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
  const [assigneeMode, setAssigneeMode] = useState<FilterMode>("include")
  const [assigneeSortDirection, setAssigneeSortDirection] =
    useState<SortDirection>(null)
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [tagMode, setTagMode] = useState<FilterMode>("include")
  const [tagSortDirection, setTagSortDirection] = useState<SortDirection>(null)
  const [dropdownFilters, setDropdownFilters] = useState<
    Record<string, DropdownFilterState>
  >({})
  const setDropdownFilter = useCallback(
    (ref: string, values: string[]) =>
      setDropdownFilters((prev) => ({
        ...prev,
        [ref]: {
          ...prev[ref],
          values,
          mode: prev[ref]?.mode ?? "include",
          sortDirection: prev[ref]?.sortDirection ?? null,
        },
      })),
    []
  )
  const setDropdownMode = useCallback(
    (ref: string, mode: FilterMode) =>
      setDropdownFilters((prev) => ({
        ...prev,
        [ref]: {
          ...prev[ref],
          values: prev[ref]?.values ?? [],
          mode,
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
          mode: prev[ref]?.mode ?? "include",
          sortDirection: direction,
        },
      })),
    []
  )
  const [updatedAfter, setUpdatedAfter] =
    useState<CaseDateFilterValue>(DEFAULT_DATE_FILTER)
  const [createdAfter, setCreatedAfter] =
    useState<CaseDateFilterValue>(DEFAULT_DATE_FILTER)
  const [updatedAtSort, setUpdatedAtSort] = useState<CasesRecencySort>("desc")
  const [limit, setLimit] = useState(50)
  const [currentCursor, setCurrentCursor] = useState<string | null>(null)
  const [cursorStack, setCursorStack] = useState<string[]>([])
  const [currentPage, setCurrentPage] = useState(0)

  // Compute query parameters for API (include mode only for now)
  // Exclude filtering is done client-side
  const queryParams = useMemo(() => {
    const normalizedSearch = searchQuery.trim()
    return {
      searchTerm: normalizedSearch.length > 0 ? normalizedSearch : undefined,
      status:
        statusFilter.length > 0 && statusMode === "include"
          ? statusFilter
          : undefined,
      priority:
        priorityFilter.length > 0 && priorityMode === "include"
          ? priorityFilter
          : undefined,
      severity:
        severityFilter.length > 0 && severityMode === "include"
          ? severityFilter
          : undefined,
      assigneeId:
        assigneeFilter.length > 0 && assigneeMode === "include"
          ? assigneeFilter
          : undefined,
      tags:
        tagFilter.length > 0 && tagMode === "include" ? tagFilter : undefined,
      // Build dropdown query params: "defRef:optRef" for include mode
      dropdown: (() => {
        const entries: string[] = []
        for (const [defRef, state] of Object.entries(dropdownFilters)) {
          if (state.values.length > 0 && state.mode === "include") {
            for (const optRef of state.values) {
              entries.push(`${defRef}:${optRef}`)
            }
          }
        }
        return entries.length > 0 ? entries : undefined
      })(),
      limit,
      cursor: currentCursor,
      orderBy: "updated_at" as const,
      sort: updatedAtSort,
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
    limit,
    currentCursor,
    updatedAtSort,
  ])

  const serverQueryKey = useMemo(
    () =>
      JSON.stringify({
        searchTerm: queryParams.searchTerm ?? null,
        status: queryParams.status ?? null,
        priority: queryParams.priority ?? null,
        severity: queryParams.severity ?? null,
        assigneeId: queryParams.assigneeId ?? null,
        tags: queryParams.tags ?? null,
        dropdown: queryParams.dropdown ?? null,
        limit: queryParams.limit,
        sort: queryParams.sort,
      }),
    [
      queryParams.searchTerm,
      queryParams.status,
      queryParams.priority,
      queryParams.severity,
      queryParams.assigneeId,
      queryParams.tags,
      queryParams.dropdown,
      queryParams.limit,
      queryParams.sort,
    ]
  )

  useEffect(() => {
    setCurrentCursor(null)
    setCursorStack([])
    setCurrentPage(0)
  }, [serverQueryKey])

  // Compute refetch interval
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

    // Refresh every 30 seconds for cases
    return 30000
  }, [autoRefresh])

  // Fetch cases
  const {
    data: casesResponse,
    isLoading,
    error,
    refetch,
  } = useQuery<CasesListCasesResponse, TracecatApiError>({
    queryKey: ["cases", workspaceId, serverQueryKey, currentCursor],
    queryFn: () =>
      casesListCases({
        workspaceId,
        ...queryParams,
      }),
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
    refetchInterval: computeRefetchInterval(),
  })

  const cases = casesResponse?.items ?? []

  const goToNextPage = useCallback(() => {
    const nextCursor = casesResponse?.next_cursor
    if (!nextCursor) return

    setCursorStack((prev) =>
      currentCursor ? [...prev, currentCursor] : [...prev]
    )
    setCurrentCursor(nextCursor)
    setCurrentPage((prev) => prev + 1)
  }, [casesResponse?.next_cursor, currentCursor])

  const goToPreviousPage = useCallback(() => {
    if (currentPage === 0) return

    const nextStack = [...cursorStack]
    const previousCursor = nextStack.pop() ?? null
    setCursorStack(nextStack)
    setCurrentCursor(previousCursor)
    setCurrentPage((prev) => Math.max(prev - 1, 0))
  }, [cursorStack, currentPage])

  const hasNextPage = Boolean(casesResponse?.has_more && casesResponse?.next_cursor)
  const hasPreviousPage = currentPage > 0

  // Apply client-side filtering (exclude mode filters + date filters)
  const filteredCases = useMemo(() => {
    if (!cases) return []

    const updatedBounds = getDateBoundsFromFilter(updatedAfter)
    const createdBounds = getDateBoundsFromFilter(createdAfter)

    const filtered = cases.filter((caseData) => {
      // Exclude mode filters (client-side)
      if (statusFilter.length > 0 && statusMode === "exclude") {
        if (statusFilter.includes(caseData.status)) return false
      }
      if (priorityFilter.length > 0 && priorityMode === "exclude") {
        if (priorityFilter.includes(caseData.priority)) return false
      }
      if (severityFilter.length > 0 && severityMode === "exclude") {
        if (severityFilter.includes(caseData.severity)) return false
      }
      if (assigneeFilter.length > 0 && assigneeMode === "exclude") {
        const assigneeId = caseData.assignee?.id ?? "unassigned"
        if (assigneeFilter.includes(assigneeId)) return false
      }
      if (tagFilter.length > 0 && tagMode === "exclude") {
        const caseTags = caseData.tags?.map((t) => t.ref) ?? []
        if (tagFilter.some((t) => caseTags.includes(t))) return false
      }
      // Dropdown exclude filters
      for (const [defRef, state] of Object.entries(dropdownFilters)) {
        if (state.values.length > 0 && state.mode === "exclude") {
          const dvMatch = caseData.dropdown_values?.find(
            (dv) => dv.definition_ref === defRef
          )
          const optRef = dvMatch?.option_ref
          if (optRef && state.values.includes(optRef)) return false
        }
      }

      // Updated after filter
      if (updatedBounds.start || updatedBounds.end) {
        const caseUpdated = new Date(caseData.updated_at)
        if (updatedBounds.start && caseUpdated < updatedBounds.start) {
          return false
        }
        if (updatedBounds.end) {
          // Set end of day for the end date
          const endOfDay = new Date(updatedBounds.end)
          endOfDay.setHours(23, 59, 59, 999)
          if (caseUpdated > endOfDay) {
            return false
          }
        }
      }

      // Created after filter
      if (createdBounds.start || createdBounds.end) {
        const caseCreated = new Date(caseData.created_at)
        if (createdBounds.start && caseCreated < createdBounds.start) {
          return false
        }
        if (createdBounds.end) {
          // Set end of day for the end date
          const endOfDay = new Date(createdBounds.end)
          endOfDay.setHours(23, 59, 59, 999)
          if (caseCreated > endOfDay) {
            return false
          }
        }
      }

      return true
    })

    // Apply sorting based on sort direction (first active sort wins)
    if (prioritySortDirection) {
      const multiplier = prioritySortDirection === "asc" ? 1 : -1
      return [...filtered].sort(
        (a, b) =>
          multiplier * (PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority])
      )
    }

    if (severitySortDirection) {
      const multiplier = severitySortDirection === "asc" ? 1 : -1
      return [...filtered].sort(
        (a, b) =>
          multiplier * (SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])
      )
    }

    if (assigneeSortDirection) {
      const multiplier = assigneeSortDirection === "asc" ? 1 : -1
      return [...filtered].sort((a, b) => {
        const aName = a.assignee?.email ?? ""
        const bName = b.assignee?.email ?? ""
        return multiplier * aName.localeCompare(bName)
      })
    }

    if (tagSortDirection) {
      const multiplier = tagSortDirection === "asc" ? 1 : -1
      return [...filtered].sort((a, b) => {
        // Sort by first tag name, empty tags go last
        const aTag = a.tags?.[0]?.name ?? ""
        const bTag = b.tags?.[0]?.name ?? ""
        if (!aTag && bTag) return 1
        if (aTag && !bTag) return -1
        return multiplier * aTag.localeCompare(bTag)
      })
    }

    return filtered
  }, [
    cases,
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
  ])

  return {
    cases: filteredCases,
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
      updatedAtSort,
      limit,
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
    setUpdatedAtSort,
    setLimit,
    goToNextPage,
    goToPreviousPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
  }
}
