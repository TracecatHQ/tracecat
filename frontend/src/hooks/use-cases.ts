"use client"

import { useQuery } from "@tanstack/react-query"
import { useCallback, useMemo, useState } from "react"
import type { DateRange } from "react-day-picker"
import {
  type CasePriority,
  type CaseReadMinimal,
  type CaseSeverity,
  type CaseStatus,
  casesSearchCases,
} from "@/client"
import type { FilterMode } from "@/components/cases/case-table-filters"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

// Preset date filter values (relative time periods)
export type CaseDatePreset = "1d" | "3d" | "1w" | "1m" | null

// Date filter can be either a preset or a custom date range
export type CaseDateFilterValue =
  | { type: "preset"; value: CaseDatePreset }
  | { type: "range"; value: DateRange }

export interface UseCasesFilters {
  searchQuery: string
  statusFilter: CaseStatus[]
  statusMode: FilterMode
  priorityFilter: CasePriority[]
  priorityMode: FilterMode
  severityFilter: CaseSeverity[]
  severityMode: FilterMode
  assigneeFilter: string[]
  assigneeMode: FilterMode
  tagFilter: string[]
  tagMode: FilterMode
  updatedAfter: CaseDateFilterValue
  createdAfter: CaseDateFilterValue
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
  setSeverityFilter: (severity: CaseSeverity[]) => void
  setSeverityMode: (mode: FilterMode) => void
  setAssigneeFilter: (assignee: string[]) => void
  setAssigneeMode: (mode: FilterMode) => void
  setTagFilter: (tags: string[]) => void
  setTagMode: (mode: FilterMode) => void
  setUpdatedAfter: (value: CaseDateFilterValue) => void
  setCreatedAfter: (value: CaseDateFilterValue) => void
  setLimit: (limit: number) => void
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

export function useCases(options: UseCasesOptions = {}): UseCasesResult {
  const { enabled = true, autoRefresh = true } = options
  const workspaceId = useWorkspaceId()

  // Filter state - multi-select with include/exclude modes
  const [searchQuery, setSearchQuery] = useState("")
  const [statusFilter, setStatusFilter] = useState<CaseStatus[]>([])
  const [statusMode, setStatusMode] = useState<FilterMode>("include")
  const [priorityFilter, setPriorityFilter] = useState<CasePriority[]>([])
  const [priorityMode, setPriorityMode] = useState<FilterMode>("include")
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity[]>([])
  const [severityMode, setSeverityMode] = useState<FilterMode>("include")
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>([])
  const [assigneeMode, setAssigneeMode] = useState<FilterMode>("include")
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [tagMode, setTagMode] = useState<FilterMode>("include")
  const [updatedAfter, setUpdatedAfter] =
    useState<CaseDateFilterValue>(DEFAULT_DATE_FILTER)
  const [createdAfter, setCreatedAfter] =
    useState<CaseDateFilterValue>(DEFAULT_DATE_FILTER)
  const [limit, setLimit] = useState(50)

  // Compute query parameters for API (include mode only for now)
  // Exclude filtering is done client-side
  const queryParams = useMemo(() => {
    return {
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
      assignee:
        assigneeFilter.length > 0 && assigneeMode === "include"
          ? assigneeFilter
          : undefined,
      tags:
        tagFilter.length > 0 && tagMode === "include" ? tagFilter : undefined,
      limit,
      orderBy: "updated_at" as const,
      sort: "desc" as const,
    }
  }, [
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
    limit,
  ])

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
    data: cases,
    isLoading,
    error,
    refetch,
  } = useQuery<CaseReadMinimal[], TracecatApiError>({
    queryKey: [
      "cases-inbox",
      workspaceId,
      queryParams.status,
      queryParams.priority,
      queryParams.severity,
      queryParams.assignee,
      queryParams.tags,
      queryParams.limit,
    ],
    queryFn: () =>
      casesSearchCases({
        workspaceId,
        ...queryParams,
      }),
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
    refetchInterval: computeRefetchInterval(),
  })

  // Apply client-side filtering (search + exclude mode filters + date filters)
  const filteredCases = useMemo(() => {
    if (!cases) return []

    const updatedBounds = getDateBoundsFromFilter(updatedAfter)
    const createdBounds = getDateBoundsFromFilter(createdAfter)

    return cases.filter((caseData) => {
      // Search filter
      if (searchQuery) {
        const query = searchQuery.toLowerCase().trim()
        const summary = caseData.summary.toLowerCase()
        const shortId = caseData.short_id.toLowerCase()
        if (!summary.includes(query) && !shortId.includes(query)) {
          return false
        }
      }

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
  }, [
    cases,
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
      severityFilter,
      severityMode,
      assigneeFilter,
      assigneeMode,
      tagFilter,
      tagMode,
      updatedAfter,
      createdAfter,
      limit,
    },
    setSearchQuery,
    setStatusFilter,
    setStatusMode,
    setPriorityFilter,
    setPriorityMode,
    setSeverityFilter,
    setSeverityMode,
    setAssigneeFilter,
    setAssigneeMode,
    setTagFilter,
    setTagMode,
    setUpdatedAfter,
    setCreatedAfter,
    setLimit,
  }
}
