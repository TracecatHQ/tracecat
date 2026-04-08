"use client"

import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { DateRange } from "react-day-picker"
import {
  type AggregateResponse,
  type CasePriority,
  type CaseReadMinimal,
  type CaseSeverity,
  type CaseStatus,
  type CasesSearchCasesResponse,
  casesAggregateCases,
  casesSearchCases,
  type FilterClause,
} from "@/client"
import {
  type CaseSortField,
  type CaseSortValue,
  DEFAULT_CASE_SORT,
} from "@/components/cases/case-sort"
import type {
  FilterMode,
  SortDirection,
} from "@/components/filters/filter-multi-select"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

const CASES_PAGE_SIZE = 100
const CASES_REFETCH_INTERVAL_MS = 120000
const CASES_FILTER_STORAGE_KEY = "cases-filters:v1"
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
const EMPTY_STAGE_COUNTS: Record<string, number> = {
  new: 0,
  in_progress: 0,
  on_hold: 0,
  resolved: 0,
  closed: 0,
  unknown: 0,
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
  sortBy: CaseSortValue
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
  setSortBy: (value: CaseSortValue) => void
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
  stageCounts: Record<string, number> | null
  isCountsLoading: boolean
  isCountsFetching: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => void
}

interface PersistedCaseDateRange {
  from?: string
  to?: string
}

type PersistedCaseDateFilter =
  | {
      type: "preset"
      value: CaseDatePreset
    }
  | {
      type: "range"
      value: PersistedCaseDateRange
    }

type PersistedCasesFilters = Omit<
  UseCasesFilters,
  "updatedAfter" | "createdAfter"
> & {
  updatedAfter: PersistedCaseDateFilter
  createdAfter: PersistedCaseDateFilter
}

type CasesFilterStateSnapshot = UseCasesFilters & {
  hydratedWorkspaceId?: string
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

const DEFAULT_CASES_FILTERS: UseCasesFilters = {
  searchQuery: "",
  sortBy: DEFAULT_CASE_SORT,
  statusFilter: [],
  statusMode: "include",
  priorityFilter: [],
  priorityMode: "include",
  prioritySortDirection: null,
  severityFilter: [],
  severityMode: "include",
  severitySortDirection: null,
  assigneeFilter: [],
  assigneeMode: "include",
  assigneeSortDirection: null,
  tagFilter: [],
  tagMode: "include",
  tagSortDirection: null,
  dropdownFilters: {},
  updatedAfter: DEFAULT_DATE_FILTER,
  createdAfter: DEFAULT_CREATED_FILTER,
}

function getCasesFilterStorageKey(workspaceId: string): string {
  return `${workspaceId}:${CASES_FILTER_STORAGE_KEY}`
}

function getDefaultCasesFilterState(): CasesFilterStateSnapshot {
  return {
    ...DEFAULT_CASES_FILTERS,
    hydratedWorkspaceId: undefined,
  }
}

function loadPersistedCasesFilterState(
  workspaceId: string | undefined
): CasesFilterStateSnapshot {
  const defaults = getDefaultCasesFilterState()
  if (!workspaceId || typeof window === "undefined") {
    return defaults
  }

  const storageKey = getCasesFilterStorageKey(workspaceId)
  try {
    const storedValue = window.localStorage.getItem(storageKey)
    if (!storedValue) {
      return { ...defaults, hydratedWorkspaceId: workspaceId }
    }

    const parsed = JSON.parse(storedValue) as Partial<PersistedCasesFilters>
    return {
      searchQuery: sanitizeSearchQuery(
        parsed.searchQuery,
        defaults.searchQuery
      ),
      sortBy: sanitizeSortBy(parsed.sortBy, defaults.sortBy),
      statusFilter: sanitizeEnumArray(
        parsed.statusFilter,
        ALL_CASE_STATUSES,
        defaults.statusFilter
      ),
      statusMode: sanitizeFilterMode(parsed.statusMode, defaults.statusMode),
      priorityFilter: sanitizeEnumArray(
        parsed.priorityFilter,
        ALL_CASE_PRIORITIES,
        defaults.priorityFilter
      ),
      priorityMode: sanitizeFilterMode(
        parsed.priorityMode,
        defaults.priorityMode
      ),
      prioritySortDirection: sanitizeSortDirection(
        parsed.prioritySortDirection,
        defaults.prioritySortDirection
      ),
      severityFilter: sanitizeEnumArray(
        parsed.severityFilter,
        ALL_CASE_SEVERITIES,
        defaults.severityFilter
      ),
      severityMode: sanitizeFilterMode(
        parsed.severityMode,
        defaults.severityMode
      ),
      severitySortDirection: sanitizeSortDirection(
        parsed.severitySortDirection,
        defaults.severitySortDirection
      ),
      assigneeFilter: sanitizeStringArray(
        parsed.assigneeFilter,
        defaults.assigneeFilter
      ),
      assigneeMode: sanitizeFilterMode(
        parsed.assigneeMode,
        defaults.assigneeMode
      ),
      assigneeSortDirection: sanitizeSortDirection(
        parsed.assigneeSortDirection,
        defaults.assigneeSortDirection
      ),
      tagFilter: sanitizeStringArray(parsed.tagFilter, defaults.tagFilter),
      tagMode: sanitizeFilterMode(parsed.tagMode, defaults.tagMode),
      tagSortDirection: sanitizeSortDirection(
        parsed.tagSortDirection,
        defaults.tagSortDirection
      ),
      dropdownFilters: sanitizeDropdownFilters(parsed.dropdownFilters),
      updatedAfter: parsePersistedDateFilter(
        parsed.updatedAfter,
        defaults.updatedAfter
      ),
      createdAfter: parsePersistedDateFilter(
        parsed.createdAfter,
        defaults.createdAfter
      ),
      hydratedWorkspaceId: workspaceId,
    }
  } catch (error) {
    console.error(error)
    try {
      window.localStorage.removeItem(storageKey)
    } catch (removeError) {
      console.error(removeError)
    }
    return { ...defaults, hydratedWorkspaceId: workspaceId }
  }
}

function persistCasesFilterState(
  workspaceId: string,
  filters: PersistedCasesFilters
): void {
  try {
    window.localStorage.setItem(
      getCasesFilterStorageKey(workspaceId),
      JSON.stringify(filters)
    )
  } catch (error) {
    console.error(error)
  }
}

function serializePersistedCasesFilters(
  filters: UseCasesFilters
): PersistedCasesFilters {
  return {
    ...filters,
    updatedAfter: serializeDateFilter(filters.updatedAfter),
    createdAfter: serializeDateFilter(filters.createdAfter),
  }
}

function isFilterMode(value: unknown): value is FilterMode {
  return value === "include" || value === "exclude"
}

function isSortDirection(value: unknown): value is SortDirection {
  return value === "asc" || value === "desc" || value === null
}

function isCaseSortField(value: unknown): value is CaseSortField {
  return (
    value === "updated_at" ||
    value === "created_at" ||
    value === "priority" ||
    value === "severity" ||
    value === "status" ||
    value === "tasks"
  )
}

function sanitizeSearchQuery(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback
}

function sanitizeStringArray(value: unknown, fallback: string[]): string[] {
  return Array.isArray(value)
    ? value.filter((item: unknown): item is string => typeof item === "string")
    : fallback
}

function sanitizeEnumArray<T extends string>(
  value: unknown,
  allowedValues: ReadonlyArray<T>,
  fallback: T[]
): T[] {
  if (!Array.isArray(value)) {
    return fallback
  }

  const allowedSet = new Set(allowedValues)
  return value.filter(
    (item: unknown): item is T =>
      typeof item === "string" && allowedSet.has(item as T)
  )
}

function sanitizeFilterMode(value: unknown, fallback: FilterMode): FilterMode {
  return isFilterMode(value) ? value : fallback
}

function sanitizeSortDirection(
  value: unknown,
  fallback: SortDirection
): SortDirection {
  return isSortDirection(value) ? value : fallback
}

function sanitizeSortBy(
  value: unknown,
  fallback: CaseSortValue
): CaseSortValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fallback
  }

  const candidate = value as {
    field?: unknown
    direction?: unknown
  }

  return isCaseSortField(candidate.field) &&
    (candidate.direction === "asc" || candidate.direction === "desc")
    ? { field: candidate.field, direction: candidate.direction }
    : fallback
}

function sanitizeDropdownFilters(
  dropdownFilters: unknown
): Record<string, DropdownFilterState> {
  if (
    !dropdownFilters ||
    typeof dropdownFilters !== "object" ||
    Array.isArray(dropdownFilters)
  ) {
    return {}
  }

  const sanitized: Record<string, DropdownFilterState> = {}
  for (const [definitionRef, state] of Object.entries(dropdownFilters)) {
    if (!state || typeof state !== "object" || Array.isArray(state)) {
      continue
    }

    const values = Array.isArray(state.values)
      ? state.values.filter(
          (value: unknown): value is string => typeof value === "string"
        )
      : null
    if (!values || !isFilterMode(state.mode)) {
      continue
    }

    sanitized[definitionRef] = {
      values,
      mode: state.mode,
      sortDirection: isSortDirection(state.sortDirection)
        ? state.sortDirection
        : null,
    }
  }

  return sanitized
}

function parsePersistedDateFilter(
  filter: PersistedCaseDateFilter | undefined,
  fallback: CaseDateFilterValue
): CaseDateFilterValue {
  if (!filter) {
    return fallback
  }

  if (filter.type === "preset") {
    if (filter.value === null) {
      return { type: "preset", value: null }
    }

    if (
      filter.value === "1d" ||
      filter.value === "3d" ||
      filter.value === "1w" ||
      filter.value === "1m"
    ) {
      return { type: "preset", value: filter.value }
    }

    return fallback
  }

  if (filter.type === "range" && typeof filter.value === "object") {
    const from =
      typeof filter.value.from === "string"
        ? new Date(filter.value.from)
        : undefined
    const to =
      typeof filter.value.to === "string"
        ? new Date(filter.value.to)
        : undefined

    return {
      type: "range",
      value: {
        from: from && !Number.isNaN(from.getTime()) ? from : undefined,
        to: to && !Number.isNaN(to.getTime()) ? to : undefined,
      },
    }
  }

  return fallback
}

function serializeDateFilter(
  filter: CaseDateFilterValue
): PersistedCaseDateFilter {
  if (filter.type === "preset") {
    return {
      type: "preset",
      value: filter.value,
    }
  }

  return {
    type: "range",
    value: {
      from: filter.value.from?.toISOString(),
      to: filter.value.to?.toISOString(),
    },
  }
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
  const initialFilterStateRef = useRef<CasesFilterStateSnapshot | null>(null)
  if (initialFilterStateRef.current === null) {
    initialFilterStateRef.current = loadPersistedCasesFilterState(workspaceId)
  }
  const initialFilterState = initialFilterStateRef.current

  const [searchQuery, setSearchQuery] = useState(initialFilterState.searchQuery)
  const [sortBy, setSortByState] = useState<CaseSortValue>(
    initialFilterState.sortBy
  )
  const [statusFilter, setStatusFilter] = useState<CaseStatus[]>(
    initialFilterState.statusFilter
  )
  const [statusMode, setStatusMode] = useState<FilterMode>(
    initialFilterState.statusMode
  )
  const [priorityFilter, setPriorityFilter] = useState<CasePriority[]>(
    initialFilterState.priorityFilter
  )
  const [priorityMode, setPriorityMode] = useState<FilterMode>(
    initialFilterState.priorityMode
  )
  const [prioritySortDirection, setPrioritySortDirectionState] =
    useState<SortDirection>(initialFilterState.prioritySortDirection)
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity[]>(
    initialFilterState.severityFilter
  )
  const [severityMode, setSeverityMode] = useState<FilterMode>(
    initialFilterState.severityMode
  )
  const [severitySortDirection, setSeveritySortDirectionState] =
    useState<SortDirection>(initialFilterState.severitySortDirection)
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>(
    initialFilterState.assigneeFilter
  )
  const [assigneeMode, setAssigneeModeState] = useState<FilterMode>(
    initialFilterState.assigneeMode
  )
  const [assigneeSortDirection, setAssigneeSortDirectionState] =
    useState<SortDirection>(initialFilterState.assigneeSortDirection)
  const [tagFilter, setTagFilter] = useState<string[]>(
    initialFilterState.tagFilter
  )
  const [tagMode, setTagModeState] = useState<FilterMode>(
    initialFilterState.tagMode
  )
  const [tagSortDirection, setTagSortDirectionState] = useState<SortDirection>(
    initialFilterState.tagSortDirection
  )

  const setSortBy = useCallback((value: CaseSortValue) => {
    setSortByState(value)
    setPrioritySortDirectionState(null)
    setSeveritySortDirectionState(null)
    setAssigneeSortDirectionState(null)
    setTagSortDirectionState(null)
    setDropdownFilters((prev) => {
      const next: Record<string, DropdownFilterState> = {}
      for (const [ref, state] of Object.entries(prev)) {
        next[ref] = { ...state, sortDirection: null }
      }
      return next
    })
  }, [])

  const setPrioritySortDirection = useCallback((direction: SortDirection) => {
    setPrioritySortDirectionState(direction)
    if (direction) {
      setSortByState({ field: "priority", direction })
      setSeveritySortDirectionState(null)
    } else {
      setSortByState(DEFAULT_CASE_SORT)
    }
  }, [])

  const setSeveritySortDirection = useCallback((direction: SortDirection) => {
    setSeveritySortDirectionState(direction)
    if (direction) {
      setSortByState({ field: "severity", direction })
      setPrioritySortDirectionState(null)
    } else {
      setSortByState(DEFAULT_CASE_SORT)
    }
  }, [])

  const setAssigneeSortDirection = useCallback((direction: SortDirection) => {
    setAssigneeSortDirectionState(direction)
  }, [])

  const setTagSortDirection = useCallback((direction: SortDirection) => {
    setTagSortDirectionState(direction)
  }, [])
  const [dropdownFilters, setDropdownFilters] = useState<
    Record<string, DropdownFilterState>
  >(initialFilterState.dropdownFilters)
  const [updatedAfter, setUpdatedAfter] = useState<CaseDateFilterValue>(
    initialFilterState.updatedAfter
  )
  const [createdAfter, setCreatedAfter] = useState<CaseDateFilterValue>(
    initialFilterState.createdAfter
  )
  const [hydratedWorkspaceId, setHydratedWorkspaceId] = useState<
    string | undefined
  >(initialFilterState.hydratedWorkspaceId)
  const hasHydratedFilters = workspaceId
    ? hydratedWorkspaceId === workspaceId
    : false

  useEffect(() => {
    if (!workspaceId) {
      setHydratedWorkspaceId(undefined)
      return
    }

    if (typeof window === "undefined") {
      return
    }

    if (hydratedWorkspaceId === workspaceId) {
      return
    }

    const nextFilterState = loadPersistedCasesFilterState(workspaceId)
    setSearchQuery(nextFilterState.searchQuery)
    setSortByState(nextFilterState.sortBy)
    setStatusFilter(nextFilterState.statusFilter)
    setStatusMode(nextFilterState.statusMode)
    setPriorityFilter(nextFilterState.priorityFilter)
    setPriorityMode(nextFilterState.priorityMode)
    setPrioritySortDirectionState(nextFilterState.prioritySortDirection)
    setSeverityFilter(nextFilterState.severityFilter)
    setSeverityMode(nextFilterState.severityMode)
    setSeveritySortDirectionState(nextFilterState.severitySortDirection)
    setAssigneeFilter(nextFilterState.assigneeFilter)
    setAssigneeModeState(nextFilterState.assigneeMode)
    setAssigneeSortDirectionState(nextFilterState.assigneeSortDirection)
    setTagFilter(nextFilterState.tagFilter)
    setTagModeState(nextFilterState.tagMode)
    setTagSortDirectionState(nextFilterState.tagSortDirection)
    setDropdownFilters(nextFilterState.dropdownFilters)
    setUpdatedAfter(nextFilterState.updatedAfter)
    setCreatedAfter(nextFilterState.createdAfter)
    setHydratedWorkspaceId(nextFilterState.hydratedWorkspaceId)
  }, [hydratedWorkspaceId, workspaceId])

  useEffect(() => {
    if (!workspaceId || !hasHydratedFilters || typeof window === "undefined") {
      return
    }

    persistCasesFilterState(
      workspaceId,
      serializePersistedCasesFilters({
        searchQuery,
        sortBy,
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
      })
    )
  }, [
    workspaceId,
    searchQuery,
    sortBy,
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
    hasHydratedFilters,
  ])

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
    return { orderBy: sortBy.field, sort: sortBy.direction }
  }, [prioritySortDirection, severitySortDirection, sortBy])

  const filtersKey = useMemo(
    () =>
      JSON.stringify({
        apiQueryParams,
        hasImpossibleEnumFilter,
      }),
    [apiQueryParams, hasImpossibleEnumFilter]
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
    enabled:
      enabled &&
      Boolean(workspaceId) &&
      hasHydratedFilters &&
      !hasImpossibleEnumFilter,
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

  // Build filter clauses for the aggregate query from the existing search params
  const aggFilterClauses = useMemo(() => {
    const clauses: FilterClause[] = []
    const qp = apiQueryParams
    if (qp.status && qp.status.length > 0) {
      clauses.push({ field: "status", op: "in", value: qp.status })
    }
    if (qp.priority && qp.priority.length > 0) {
      clauses.push({ field: "priority", op: "in", value: qp.priority })
    }
    if (qp.severity && qp.severity.length > 0) {
      clauses.push({ field: "severity", op: "in", value: qp.severity })
    }
    if (qp.assigneeId && qp.assigneeId.length > 0) {
      clauses.push({ field: "assignee_id", op: "in", value: qp.assigneeId })
    }
    if (qp.tags && qp.tags.length > 0) {
      clauses.push({ field: "tags", op: "has_any", value: qp.tags })
    }
    if (qp.startTime) {
      clauses.push({ field: "created_at", op: "gte", value: qp.startTime })
    }
    if (qp.endTime) {
      clauses.push({ field: "created_at", op: "lte", value: qp.endTime })
    }
    if (qp.updatedAfter) {
      clauses.push({ field: "updated_at", op: "gte", value: qp.updatedAfter })
    }
    if (qp.updatedBefore) {
      clauses.push({ field: "updated_at", op: "lte", value: qp.updatedBefore })
    }
    if (qp.dropdown && qp.dropdown.length > 0) {
      // Group dropdown values by definition ref
      const byDef: Record<string, string[]> = {}
      for (const item of qp.dropdown) {
        const colonIdx = item.indexOf(":")
        if (colonIdx > 0) {
          const defRef = item.slice(0, colonIdx)
          const optRef = item.slice(colonIdx + 1)
          ;(byDef[defRef] ??= []).push(optRef)
        }
      }
      for (const [defRef, optRefs] of Object.entries(byDef)) {
        clauses.push({
          field: `dropdown.${defRef}`,
          op: "in",
          value: optRefs,
        })
      }
    }
    return clauses
  }, [apiQueryParams])

  const {
    data: aggregateData,
    isLoading: isCountsLoading,
    isFetching: isCountsFetching,
    refetch: refetchCounts,
  } = useQuery<AggregateResponse, TracecatApiError>({
    queryKey: ["cases", "aggregate", workspaceId, filtersKey],
    queryFn: () =>
      casesAggregateCases({
        workspaceId,
        requestBody: {
          group_by: ["status"],
          agg: [{ func: "count" }],
          filter: aggFilterClauses.length > 0 ? aggFilterClauses : [],
          search: apiQueryParams.searchTerm ?? null,
        },
      }),
    enabled:
      enabled &&
      Boolean(workspaceId) &&
      hasHydratedFilters &&
      !hasImpossibleEnumFilter,
    retry: retryHandler,
    refetchInterval: computeRefetchInterval(),
    refetchOnWindowFocus: false,
    staleTime: 60000,
  })

  const visibleRowsData = hasHydratedFilters ? rowsData : undefined
  const visibleAggregateData = hasHydratedFilters ? aggregateData : undefined

  const flattenedCases = useMemo(() => {
    const pages = visibleRowsData?.pages ?? []
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
  }, [visibleRowsData?.pages])

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

  const cases = hasImpossibleEnumFilter ? [] : sortedCases

  const totalFilteredCaseEstimate = hasImpossibleEnumFilter
    ? 0
    : visibleAggregateData
      ? visibleAggregateData.items.reduce(
          (sum, item) => sum + ((item.count as number) ?? 0),
          0
        )
      : (visibleRowsData?.pages[0]?.total_estimate ?? null)
  const isHydrationPending =
    enabled && Boolean(workspaceId) && !hasHydratedFilters

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
    cases,
    isLoading: isHydrationPending ? true : isLoading,
    error: error ?? null,
    refetch,
    filters: {
      searchQuery,
      sortBy,
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
    setSortBy,
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
      : visibleAggregateData
        ? Object.fromEntries(
            visibleAggregateData.items.map((item) => [
              item.status as string,
              item.count as number,
            ])
          )
        : null,
    isCountsLoading: hasImpossibleEnumFilter
      ? false
      : isHydrationPending || isCountsLoading,
    isCountsFetching: hasImpossibleEnumFilter
      ? false
      : hasHydratedFilters && isCountsFetching,
    hasNextPage: hasImpossibleEnumFilter ? false : Boolean(hasNextPage),
    isFetchingNextPage: hasImpossibleEnumFilter
      ? false
      : hasHydratedFilters && isFetchingNextPage,
    fetchNextPage: handleFetchNextPage,
  }
}
