"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { v4 as uuid4 } from "uuid"
import {
  type CasePriority,
  type CaseReadMinimal,
  type CaseSearchAggregateRead,
  type CaseSearchRequest,
  type CaseSeverity,
  type CaseStatus,
  type CasesQueryCaseAggregatesResponse,
  type CasesQueryCasesResponse,
  type CaseViewEmptyRules,
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
import {
  ALL_CASE_PRIORITIES,
  ALL_CASE_SEVERITIES,
  ALL_CASE_STATUSES,
  type CaseDateFilterValue,
  type CaseDatePreset,
  DEFAULT_CASES_FILTERS,
  type DropdownFilterState,
  emptyRulesMatchNone,
  getCaseSearchTerm,
  resolveEnumIncludeFilter,
  type UseCasesFilters,
} from "@/lib/case-query"
import {
  type CaseViewQuery,
  filtersToViewQuery,
  mergeViewQuery,
} from "@/lib/case-views"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useInfiniteQuery, useQuery } from "@/lib/query"
import { withAbortSignal } from "@/lib/query-cancellation"
import { useWorkspaceId } from "@/providers/workspace-id"

// The pure query pieces moved to lib/case-query; existing callers import them
// from here.
export {
  ALL_CASE_PRIORITIES,
  ALL_CASE_SEVERITIES,
  ALL_CASE_STATUSES,
  type CaseDateFilterValue,
  type CaseDatePreset,
  DEFAULT_CASES_FILTERS,
  DEFAULT_CREATED_FILTER,
  DEFAULT_CREATED_PRESET,
  DEFAULT_DATE_FILTER,
  type DropdownFilterState,
  getCaseSearchTerm,
  presetToWindow,
  resolveEnumIncludeFilter,
  type UseCasesFilters,
} from "@/lib/case-query"

const CASES_PAGE_SIZE = 100
const CASES_SEARCH_DEBOUNCE_MS = 300
const CASES_REFETCH_INTERVAL_MS = 120000
const CASES_FILTER_STORAGE_KEY = "cases-filters:v1"
const EMPTY_STAGE_COUNTS: CaseSearchAggregateRead["status_groups"] = {
  new: 0,
  in_progress: 0,
  on_hold: 0,
  resolved: 0,
  closed: 0,
  unknown: 0,
  other: 0,
}

export interface UseCasesOptions {
  enabled?: boolean
  autoRefresh?: boolean
  fieldIds?: string[]
  includeDurations?: boolean
  /**
   * Start from these filters instead of the ones saved in this browser, and
   * never save changes there. A saved view passes its filters here; the page
   * mounts a fresh hook per view, so the user's own filters stay untouched.
   */
  initialFilters?: UseCasesFilters
  /** Conditions the filter bar cannot show, sent with every request. */
  extraQuery?: CaseViewQuery
  /** A view's rules left empty by deletions; they match nothing until refilled. */
  emptyRules?: CaseViewEmptyRules | null
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
  /** Replace every filter at once, for example when opening a saved view. */
  replaceFilters: (
    filters: UseCasesFilters,
    options?: { resetPagination?: boolean }
  ) => void
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

/** Workspace case search with server-owned exact-reference promotion. */
export function useCases(options: UseCasesOptions = {}): UseCasesResult {
  const {
    enabled = true,
    autoRefresh = true,
    fieldIds = [],
    includeDurations = false,
    initialFilters,
    extraQuery,
    emptyRules,
  } = options
  // Fixed for the hook's lifetime: a view's hook never saves to this browser.
  const [persist] = useState(initialFilters === undefined)
  const workspaceId = useWorkspaceId()
  const normalizedFieldIds = useMemo(
    () => [...new Set(fieldIds)].sort(),
    [fieldIds]
  )
  const initialFilterStateRef = useRef<CasesFilterStateSnapshot | null>(null)
  if (initialFilterStateRef.current === null) {
    initialFilterStateRef.current = initialFilters
      ? { ...initialFilters, hydratedWorkspaceId: workspaceId }
      : loadPersistedCasesFilterState(workspaceId)
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

  const replaceFilters = useCallback(
    (next: UseCasesFilters, { resetPagination = false } = {}) => {
      setSearchQueryState(next.searchQuery)
      // Only start a new page sequence when asked, or when the search term
      // changes, so reopening an unchanged view reuses its cached pages.
      applySearch(next.searchQuery, resetPagination)
      setSearchMode(next.searchMode ?? "full_text")
      setSearchOrder(next.searchOrder ?? "relevance")
      setSortByState(next.sortBy)
      setStatusFilter(next.statusFilter)
      setStatusMode(next.statusMode)
      setPriorityFilter(next.priorityFilter)
      setPriorityMode(next.priorityMode)
      setSeverityFilter(next.severityFilter)
      setSeverityMode(next.severityMode)
      setAssigneeFilter(next.assigneeFilter)
      setAssigneeModeState(next.assigneeMode)
      setTagFilter(next.tagFilter)
      setTagModeState(next.tagMode)
      setDropdownFilters(next.dropdownFilters)
      setUpdatedAfter(next.updatedAfter)
      setCreatedAfter(next.createdAfter)
      setIncludeSubCases(next.includeSubCases)
    },
    [applySearch]
  )

  useEffect(() => {
    if (!workspaceId) {
      setHydratedWorkspaceId(undefined)
      return
    }

    // A view's filters come from the view, not from this browser.
    if (!persist || typeof window === "undefined") {
      return
    }

    if (hydratedWorkspaceId === workspaceId) {
      return
    }

    const nextFilterState = loadPersistedCasesFilterState(workspaceId)
    replaceFilters(nextFilterState, { resetPagination: true })
    setHydratedWorkspaceId(nextFilterState.hydratedWorkspaceId)
  }, [hydratedWorkspaceId, workspaceId, replaceFilters, persist])

  useEffect(() => {
    if (
      !persist ||
      !workspaceId ||
      !hasHydratedFilters ||
      typeof window === "undefined"
    ) {
      return
    }

    persistCasesFilterState(
      workspaceId,
      serializePersistedCasesFilters({
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
    persist,
    workspaceId,
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
    const requestFilters: UseCasesFilters = {
      ...DEFAULT_CASES_FILTERS,
      searchQuery: appliedSearchQuery,
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
    }
    // One builder for both the request and a saved view, so what a view saves
    // is exactly what the list searched. A view's own conditions apply until
    // the filter bar sets the same group.
    const query = mergeViewQuery(
      extraQuery ?? {},
      filtersToViewQuery(requestFilters)
    )
    const matchesNone = [
      resolveEnumIncludeFilter(statusFilter, statusMode, ALL_CASE_STATUSES),
      resolveEnumIncludeFilter(
        priorityFilter,
        priorityMode,
        ALL_CASE_PRIORITIES
      ),
      resolveEnumIncludeFilter(
        severityFilter,
        severityMode,
        ALL_CASE_SEVERITIES
      ),
    ].some((resolved) => resolved.matchesNone)
    return {
      // Keep the search keys and the hierarchy explicit on the wire.
      apiQueryParams: {
        ...query,
        search_term: query.search_term,
        search_mode: query.search_mode,
        hierarchy: (query.hierarchy ?? "all") as CaseHierarchyFilter,
      },
      hasImpossibleEnumFilter:
        matchesNone ||
        emptyRulesMatchNone(emptyRules, { tagFilter, dropdownFilters }),
    }
  }, [
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
    extraQuery,
    emptyRules,
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
      }),
    [apiQueryParams, hasImpossibleEnumFilter, appliedSearch.revision]
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
    replaceFilters,
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
