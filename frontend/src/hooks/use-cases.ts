"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { DateRange } from "react-day-picker"
import { v4 as uuid4 } from "uuid"
import {
  type CasePriority,
  type CaseQuery,
  type CaseReadMinimal,
  type CaseSearchAggregateRead,
  type CaseSearchRequest,
  type CaseSeverity,
  type CaseStatus,
  type CasesQueryCaseAggregatesResponse,
  type CasesQueryCasesResponse,
  casesQueryCaseAggregates,
  casesQueryCases,
  type SearchMode,
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
import type { CaseHierarchyFilter } from "@/hooks/use-case-search-infinite"
import { restoreCaseFilter } from "@/lib/case-filters"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useInfiniteQuery, useQuery } from "@/lib/query"
import { withAbortSignal } from "@/lib/query-cancellation"
import { useWorkspaceId } from "@/providers/workspace-id"

const CASES_PAGE_SIZE = 100
const CASES_SEARCH_DEBOUNCE_MS = 300
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
const EMPTY_STAGE_COUNTS: CaseSearchAggregateRead["status_groups"] = {
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

const MIN_LITERAL_SEARCH_LENGTH = 3
const LETTER_OR_NUMBER = /[\p{L}\p{N}]/u

// Input the API would reject (punctuation-only words, too-short literals) is
// treated as no search, so typing "-" does not replace the list with an error.
function getCaseSearchTerm(query: string, mode: SearchMode): string {
  const trimmed = query.trim()
  if (mode === "literal") {
    return trimmed.length >= MIN_LITERAL_SEARCH_LENGTH ? query : ""
  }
  return LETTER_OR_NUMBER.test(trimmed) ? trimmed : ""
}

export interface UseCasesFilters {
  structuredFilters?: CaseQuery["filters"]
  searchQuery: string
  searchMode?: SearchMode
  searchOrder?: "relevance" | "column"
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
  includeSubCases: boolean
}

export interface UseCasesOptions {
  enabled?: boolean
  autoRefresh?: boolean
  fieldIds?: string[]
  includeDurations?: boolean
}

export interface UseCasesResult {
  cases: CaseReadMinimal[]
  isLoading: boolean
  error: Error | null
  refetch: () => void
  filters: UseCasesFilters
  appliedSearchQuery: string
  effectiveHierarchy: CaseHierarchyFilter
  isSearchPending: boolean
  setSearchMode: (mode: SearchMode) => void
  setSearchQuery: (query: string) => void
  setSortBy: (value: CaseSortValue) => void
  setStructuredFilters: (filters: CaseQuery["filters"]) => void
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
  setIncludeSubCases: (value: boolean) => void
  totalFilteredCaseEstimate: number | null
  stageCounts: CaseSearchAggregateRead["status_groups"] | null
  isCountsLoading: boolean
  isCountsFetching: boolean
  isCountsError: boolean
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
  structuredFilters: null,
  searchQuery: "",
  searchMode: "full_text",
  searchOrder: "relevance",
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
  includeSubCases: false,
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
    return normalizeCasesSortState({
      structuredFilters: restoreCaseFilter(parsed.structuredFilters),
      searchMode: parsed.searchMode === "literal" ? "literal" : "full_text",
      searchOrder:
        parsed.searchOrder === "column" || parsed.searchOrder === "relevance"
          ? parsed.searchOrder
          : undefined,
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
      includeSubCases:
        typeof parsed.includeSubCases === "boolean"
          ? parsed.includeSubCases
          : defaults.includeSubCases,
      hydratedWorkspaceId: workspaceId,
    })
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

function normalizeCasesSortState(
  state: CasesFilterStateSnapshot
): CasesFilterStateSnapshot {
  // Preserve the effective order of legacy page-local sorts, then synchronize
  // their controls with the single canonical server sort.
  let sortBy = state.sortBy
  if (state.assigneeSortDirection) {
    sortBy = { field: "assignee", direction: state.assigneeSortDirection }
  } else if (state.tagSortDirection) {
    sortBy = { field: "tags", direction: state.tagSortDirection }
  } else if (state.prioritySortDirection) {
    sortBy = { field: "priority", direction: state.prioritySortDirection }
  } else if (state.severitySortDirection) {
    sortBy = { field: "severity", direction: state.severitySortDirection }
  }
  return {
    ...state,
    searchOrder:
      state.searchOrder ??
      (sortBy.field === DEFAULT_CASE_SORT.field &&
      sortBy.direction === DEFAULT_CASE_SORT.direction
        ? "relevance"
        : "column"),
    sortBy,
    prioritySortDirection:
      sortBy.field === "priority" ? sortBy.direction : null,
    severitySortDirection:
      sortBy.field === "severity" ? sortBy.direction : null,
    assigneeSortDirection:
      sortBy.field === "assignee" ? sortBy.direction : null,
    tagSortDirection: sortBy.field === "tags" ? sortBy.direction : null,
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
    value === "tasks" ||
    value === "assignee" ||
    value === "tags"
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

/** Workspace case search with server-owned exact-reference promotion. */
export function useCases(options: UseCasesOptions = {}): UseCasesResult {
  const {
    enabled = true,
    autoRefresh = true,
    fieldIds = [],
    includeDurations = false,
  } = options
  const workspaceId = useWorkspaceId()
  const normalizedFieldIds = useMemo(
    () => [...new Set(fieldIds)].sort(),
    [fieldIds]
  )
  const initialFilterStateRef = useRef<CasesFilterStateSnapshot | null>(null)
  if (initialFilterStateRef.current === null) {
    initialFilterStateRef.current = loadPersistedCasesFilterState(workspaceId)
  }
  const initialFilterState = initialFilterStateRef.current

  const [searchMode, setSearchMode] = useState<SearchMode>(
    initialFilterState.searchMode ?? "full_text"
  )
  const [searchOrder, setSearchOrder] = useState<"relevance" | "column">(
    initialFilterState.searchOrder ?? "relevance"
  )
  const [searchQuery, setSearchQueryState] = useState(
    initialFilterState.searchQuery
  )
  const [appliedSearch, setAppliedSearch] = useState({
    query: initialFilterState.searchQuery,
    revision: "",
  })
  const appliedSearchQuery = getCaseSearchTerm(appliedSearch.query, searchMode)
  const normalizedSearchInput = getCaseSearchTerm(searchQuery, searchMode)
  const applySearch = useCallback(
    (query: string, resetPagination = false) => {
      // Query cache entries can outlive this hook, so each application needs a
      // generation that remains unique across remounts.
      const revision = uuid4()
      setAppliedSearch((previous) => {
        if (previous.query === query && !resetPagination) return previous
        const matchingChanged =
          getCaseSearchTerm(previous.query, searchMode) !==
          getCaseSearchTerm(query, searchMode)
        return {
          query,
          revision:
            resetPagination || matchingChanged ? revision : previous.revision,
        }
      })
    },
    [searchMode]
  )
  useEffect(() => {
    const rawQuery = searchQuery.trim() ? searchQuery : ""
    if (rawQuery === appliedSearch.query) return
    const timeout = setTimeout(
      () => applySearch(rawQuery),
      CASES_SEARCH_DEBOUNCE_MS
    )
    return () => clearTimeout(timeout)
  }, [searchQuery, appliedSearch.query, applySearch])
  const setSearchQuery = useCallback(
    (query: string) => {
      setSearchQueryState(query)
      if (!query.trim()) {
        applySearch("")
      }
    },
    [applySearch]
  )
  const [structuredFilters, setStructuredFiltersState] = useState<
    CaseQuery["filters"]
  >(initialFilterState.structuredFilters ?? null)
  const [structuredRevision, setStructuredRevision] = useState("")
  const setStructuredFilters = useCallback((value: CaseQuery["filters"]) => {
    setStructuredFiltersState(value)
    setStructuredRevision(uuid4())
  }, [])
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
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity[]>(
    initialFilterState.severityFilter
  )
  const [severityMode, setSeverityMode] = useState<FilterMode>(
    initialFilterState.severityMode
  )
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>(
    initialFilterState.assigneeFilter
  )
  const [assigneeMode, setAssigneeModeState] = useState<FilterMode>(
    initialFilterState.assigneeMode
  )
  const [tagFilter, setTagFilter] = useState<string[]>(
    initialFilterState.tagFilter
  )
  const [tagMode, setTagModeState] = useState<FilterMode>(
    initialFilterState.tagMode
  )

  const setSortBy = useCallback((value: CaseSortValue) => {
    setSearchOrder(value.field === "relevance" ? "relevance" : "column")
    setSortByState(value.field === "relevance" ? DEFAULT_CASE_SORT : value)
    setDropdownFilters((prev) => {
      const next: Record<string, DropdownFilterState> = {}
      for (const [ref, state] of Object.entries(prev)) {
        next[ref] = { ...state, sortDirection: null }
      }
      return next
    })
  }, [])

  const setPrioritySortDirection = useCallback(
    (direction: SortDirection) => {
      if (direction) {
        setSortBy({ field: "priority", direction })
      } else {
        setSortBy(DEFAULT_CASE_SORT)
      }
    },
    [setSortBy]
  )

  const setSeveritySortDirection = useCallback(
    (direction: SortDirection) => {
      if (direction) {
        setSortBy({ field: "severity", direction })
      } else {
        setSortBy(DEFAULT_CASE_SORT)
      }
    },
    [setSortBy]
  )

  const setAssigneeSortDirection = useCallback(
    (direction: SortDirection) => {
      setSortBy(
        direction ? { field: "assignee", direction } : DEFAULT_CASE_SORT
      )
    },
    [setSortBy]
  )

  const setTagSortDirection = useCallback(
    (direction: SortDirection) => {
      setSortBy(direction ? { field: "tags", direction } : DEFAULT_CASE_SORT)
    },
    [setSortBy]
  )
  const [dropdownFilters, setDropdownFilters] = useState<
    Record<string, DropdownFilterState>
  >(initialFilterState.dropdownFilters)
  const [updatedAfter, setUpdatedAfter] = useState<CaseDateFilterValue>(
    initialFilterState.updatedAfter
  )
  const [createdAfter, setCreatedAfter] = useState<CaseDateFilterValue>(
    initialFilterState.createdAfter
  )
  const [includeSubCases, setIncludeSubCases] = useState<boolean>(
    initialFilterState.includeSubCases
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
    setStructuredFilters(nextFilterState.structuredFilters ?? null)
    setSearchQueryState(nextFilterState.searchQuery)
    applySearch(nextFilterState.searchQuery, true)
    setSearchMode(nextFilterState.searchMode ?? "full_text")
    setSearchOrder(nextFilterState.searchOrder ?? "relevance")
    setSortByState(nextFilterState.sortBy)
    setStatusFilter(nextFilterState.statusFilter)
    setStatusMode(nextFilterState.statusMode)
    setPriorityFilter(nextFilterState.priorityFilter)
    setPriorityMode(nextFilterState.priorityMode)
    setSeverityFilter(nextFilterState.severityFilter)
    setSeverityMode(nextFilterState.severityMode)
    setAssigneeFilter(nextFilterState.assigneeFilter)
    setAssigneeModeState(nextFilterState.assigneeMode)
    setTagFilter(nextFilterState.tagFilter)
    setTagModeState(nextFilterState.tagMode)
    setDropdownFilters(nextFilterState.dropdownFilters)
    setUpdatedAfter(nextFilterState.updatedAfter)
    setCreatedAfter(nextFilterState.createdAfter)
    setIncludeSubCases(nextFilterState.includeSubCases)
    setHydratedWorkspaceId(nextFilterState.hydratedWorkspaceId)
  }, [hydratedWorkspaceId, workspaceId, applySearch, setStructuredFilters])

  useEffect(() => {
    if (!workspaceId || !hasHydratedFilters || typeof window === "undefined") {
      return
    }

    persistCasesFilterState(
      workspaceId,
      serializePersistedCasesFilters({
        structuredFilters,
        searchQuery,
        searchMode,
        searchOrder,
        sortBy,
        statusFilter,
        statusMode,
        priorityFilter,
        priorityMode,
        prioritySortDirection:
          sortBy.field === "priority" ? sortBy.direction : null,
        severityFilter,
        severityMode,
        severitySortDirection:
          sortBy.field === "severity" ? sortBy.direction : null,
        assigneeFilter,
        assigneeMode,
        assigneeSortDirection:
          sortBy.field === "assignee" ? sortBy.direction : null,
        tagFilter,
        tagMode,
        tagSortDirection: sortBy.field === "tags" ? sortBy.direction : null,
        dropdownFilters,
        updatedAfter,
        createdAfter,
        includeSubCases,
      })
    )
  }, [
    workspaceId,
    structuredFilters,
    searchQuery,
    searchMode,
    searchOrder,
    sortBy,
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
    includeSubCases,
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
    const normalizedSearch = appliedSearchQuery
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
        filters: structuredFilters,
        search_term: normalizedSearch.length > 0 ? normalizedSearch : undefined,
        search_mode: normalizedSearch.length > 0 ? searchMode : undefined,
        status: resolvedStatus.values,
        priority: resolvedPriority.values,
        severity: resolvedSeverity.values,
        assignee_id:
          assigneeFilter.length > 0 && assigneeMode === "include"
            ? assigneeFilter
            : undefined,
        tags:
          tagFilter.length > 0 && tagMode === "include" ? tagFilter : undefined,
        dropdown: dropdownIncludes.length > 0 ? dropdownIncludes : undefined,
        start_time: createdBounds.start?.toISOString(),
        end_time: createdBounds.end
          ? toEndOfDay(createdBounds.end).toISOString()
          : undefined,
        updated_after: updatedBounds.start?.toISOString(),
        updated_before: updatedBounds.end
          ? toEndOfDay(updatedBounds.end).toISOString()
          : undefined,
        // Searching also matches sub-cases, so they are never unreachable.
        hierarchy: (includeSubCases || appliedSearchQuery
          ? "all"
          : "top_level") as CaseHierarchyFilter,
      },
      hasImpossibleEnumFilter:
        resolvedStatus.matchesNone ||
        resolvedPriority.matchesNone ||
        resolvedSeverity.matchesNone,
    }
  }, [
    structuredFilters,
    appliedSearchQuery,
    searchMode,
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
    includeSubCases,
  ])

  const effectiveSortBy: CaseSortValue =
    searchMode === "full_text" &&
    appliedSearchQuery &&
    searchOrder === "relevance"
      ? { field: "relevance", direction: "desc" }
      : sortBy
  const prioritySortDirection =
    effectiveSortBy.field === "priority" ? effectiveSortBy.direction : null
  const severitySortDirection =
    effectiveSortBy.field === "severity" ? effectiveSortBy.direction : null
  const assigneeSortDirection =
    effectiveSortBy.field === "assignee" ? effectiveSortBy.direction : null
  const tagSortDirection =
    effectiveSortBy.field === "tags" ? effectiveSortBy.direction : null
  const serverSortParams = {
    orderBy: effectiveSortBy.field,
    sort: effectiveSortBy.direction,
  }

  const filtersKey = useMemo(
    () =>
      JSON.stringify({
        apiQueryParams,
        hasImpossibleEnumFilter,
        // A new applied search starts at page one even when returning to a
        // previously cached term, including an immediately cleared input.
        searchRevision: appliedSearch.revision,
        structuredRevision,
      }),
    [
      apiQueryParams,
      hasImpossibleEnumFilter,
      appliedSearch.revision,
      structuredRevision,
    ]
  )

  const rowsKey = useMemo(
    () =>
      JSON.stringify({
        filtersKey,
        orderBy: serverSortParams.orderBy,
        sort: serverSortParams.sort,
        fieldIds: normalizedFieldIds,
        includeDurations,
      }),
    [
      filtersKey,
      serverSortParams.orderBy,
      serverSortParams.sort,
      normalizedFieldIds,
      includeDurations,
    ]
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
  } = useInfiniteQuery<CasesQueryCasesResponse, TracecatApiError>({
    queryKey: ["cases", workspaceId, rowsKey],
    queryFn: ({ pageParam, signal }) =>
      withAbortSignal(
        signal,
        casesQueryCases({
          workspaceId,
          requestBody: {
            ...apiQueryParams,
            promote_exact_match: Boolean(appliedSearchQuery) || undefined,
            match_none: hasImpossibleEnumFilter || undefined,
            order_by: serverSortParams.orderBy,
            sort: serverSortParams.sort,
            limit: CASES_PAGE_SIZE,
            cursor: (pageParam as string | null) ?? undefined,
            field_ids:
              normalizedFieldIds.length > 0 ? normalizedFieldIds : undefined,
            include_durations: includeDurations || undefined,
            include_count: false,
          } satisfies CaseSearchRequest,
        })
      ),
    enabled:
      enabled &&
      Boolean(workspaceId) &&
      hasHydratedFilters &&
      (!hasImpossibleEnumFilter || Boolean(appliedSearchQuery)),
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
    // The layout and header render these failures inline.
    meta: { suppressErrorToast: true },
  })

  const {
    data: aggregateData,
    isLoading: isCountsLoading,
    isFetching: isCountsFetching,
    isError: isCountsError,
    refetch: refetchCounts,
  } = useQuery<CasesQueryCaseAggregatesResponse, TracecatApiError>({
    queryKey: ["cases", "aggregate", workspaceId, filtersKey],
    queryFn: ({ signal }) =>
      withAbortSignal(
        signal,
        casesQueryCaseAggregates({
          workspaceId,
          requestBody: apiQueryParams,
        })
      ),
    enabled:
      enabled &&
      Boolean(workspaceId) &&
      hasHydratedFilters &&
      !hasImpossibleEnumFilter,
    retry: retryHandler,
    refetchInterval: computeRefetchInterval(),
    refetchOnWindowFocus: false,
    staleTime: 60000,
    // The layout and header render these failures inline.
    meta: { suppressErrorToast: true },
  })

  const visibleRowsData = hasHydratedFilters ? rowsData : undefined
  const visibleAggregateData =
    hasHydratedFilters && !isCountsError ? aggregateData : undefined

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

  const cases = flattenedCases

  const totalFilteredCaseEstimate = hasImpossibleEnumFilter
    ? 0
    : (visibleAggregateData?.total ?? null)
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
    appliedSearchQuery,
    isSearchPending: normalizedSearchInput !== appliedSearchQuery,
    effectiveHierarchy: apiQueryParams.hierarchy,
    filters: {
      structuredFilters,
      searchQuery,
      searchMode,
      searchOrder,
      sortBy: effectiveSortBy,
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
      includeSubCases,
    },
    setSearchMode,
    setStructuredFilters,
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
    setIncludeSubCases,
    totalFilteredCaseEstimate,
    stageCounts: hasImpossibleEnumFilter
      ? EMPTY_STAGE_COUNTS
      : (visibleAggregateData?.status_groups ?? null),
    isCountsLoading: hasImpossibleEnumFilter
      ? false
      : isHydrationPending || isCountsLoading,
    isCountsFetching: hasImpossibleEnumFilter
      ? false
      : hasHydratedFilters && isCountsFetching,
    isCountsError:
      enabled &&
      hasHydratedFilters &&
      !hasImpossibleEnumFilter &&
      isCountsError,
    hasNextPage: hasImpossibleEnumFilter ? false : Boolean(hasNextPage),
    isFetchingNextPage: hasImpossibleEnumFilter
      ? false
      : hasHydratedFilters && isFetchingNextPage,
    fetchNextPage: handleFetchNextPage,
  }
}
