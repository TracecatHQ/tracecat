import type {
  CaseQuery_Input,
  CaseQuery_Output,
  CaseStatus,
  CaseViewEmptyRules,
  CaseViewRead,
  CaseViewSort,
  RelativeTimeWindow,
} from "@/client"
import {
  type CaseSortField,
  type CaseSortValue,
  DEFAULT_CASE_SORT,
} from "@/components/cases/case-sort"
import {
  ALL_CASE_PRIORITIES,
  ALL_CASE_SEVERITIES,
  ALL_CASE_STATUSES,
  type CaseDateFilterValue,
  type CaseDatePreset,
  DEFAULT_CASES_FILTERS,
  DEFAULT_DATE_FILTER,
  type DropdownFilterState,
  emptyRulesMatchNone,
  formatDropdownEntry,
  getCaseSearchTerm,
  parseDropdownEntry,
  presetToWindow,
  resolveEnumIncludeFilter,
  toEndOfDay,
  type UseCasesFilters,
} from "@/lib/case-query"

const DATE_PRESETS: NonNullable<CaseDatePreset>[] = ["1d", "3d", "1w", "1m"]

/**
 * Query fields the filter bar can show. Anything else in a view's query is
 * kept as-is in {@link CaseViewFilters.extraQuery} and shown read-only.
 */
const FILTER_BAR_FIELDS = new Set<keyof CaseQuery_Output>([
  "search_term",
  "search_mode",
  "hierarchy",
  "status",
  "priority",
  "severity",
  "assignee_id",
  "tags",
  "dropdown",
  "start_time",
  "end_time",
  "created_within",
  "updated_after",
  "updated_before",
  "updated_within",
])

/** Query fields that are part of a view's matching criteria. */
export type CaseViewQuery = Omit<CaseQuery_Input, "view_id">

/** A view, split into what the filter bar shows and what it cannot. */
export interface CaseViewFilters {
  filters: UseCasesFilters
  /** Conditions the filter bar cannot show, sent with every request. */
  extraQuery: CaseViewQuery
  /** Short labels for {@link extraQuery}, such as "Created by me". */
  extraLabels: string[]
  /**
   * Rules left empty by deletions, as the server reports them. They match
   * nothing until refilled. Null when the view was not read on its own.
   */
  emptyRules: CaseViewEmptyRules | null
  /** The view's query as it runs (deleted refs removed), for change tracking. */
  savedQuery: CaseViewQuery
}

function windowToPreset(window: RelativeTimeWindow): CaseDatePreset | null {
  for (const preset of DATE_PRESETS) {
    const candidate = presetToWindow(preset)
    if (candidate?.amount === window.amount && candidate.unit === window.unit) {
      return preset
    }
  }
  return null
}

function dateFilterToQuery(filter: CaseDateFilterValue): {
  window?: RelativeTimeWindow
  start?: string
  end?: string
} {
  if (filter.type === "preset") {
    const window = presetToWindow(filter.value)
    return window ? { window } : {}
  }
  return {
    start: filter.value.from?.toISOString(),
    end: filter.value.to
      ? toEndOfDay(filter.value.to).toISOString()
      : undefined,
  }
}

/**
 * Turn the filter bar's state into the query a saved view stores.
 *
 * Exclusions become the list of values to include, so the stored query has
 * one shape however the user picked it.
 */
export function filtersToViewQuery(filters: UseCasesFilters): CaseViewQuery {
  const query: CaseViewQuery = {}
  const searchMode = filters.searchMode ?? "full_text"
  const searchTerm = getCaseSearchTerm(filters.searchQuery, searchMode)
  if (searchTerm) {
    query.search_term = searchTerm
    query.search_mode = searchMode
  }
  const status = resolveEnumIncludeFilter(
    filters.statusFilter,
    filters.statusMode,
    ALL_CASE_STATUSES
  )
  const priority = resolveEnumIncludeFilter(
    filters.priorityFilter,
    filters.priorityMode,
    ALL_CASE_PRIORITIES
  )
  const severity = resolveEnumIncludeFilter(
    filters.severityFilter,
    filters.severityMode,
    ALL_CASE_SEVERITIES
  )
  if (status.values) query.status = status.values
  if (priority.values) query.priority = priority.values
  if (severity.values) query.severity = severity.values
  // The filter bar only offers "include" for these; an old stored "exclude"
  // selection is ignored, as the search request always did.
  if (filters.assigneeFilter.length > 0 && filters.assigneeMode === "include") {
    query.assignee_id = filters.assigneeFilter
  }
  if (filters.tagFilter.length > 0 && filters.tagMode === "include") {
    query.tags = filters.tagFilter
  }
  const dropdown = Object.entries(filters.dropdownFilters).flatMap(
    ([ref, state]) =>
      state.mode === "include"
        ? state.values.map((option) => formatDropdownEntry(ref, option))
        : []
  )
  if (dropdown.length > 0) query.dropdown = dropdown

  const created = dateFilterToQuery(filters.createdAfter)
  if (created.window) query.created_within = created.window
  if (created.start) query.start_time = created.start
  if (created.end) query.end_time = created.end
  const updated = dateFilterToQuery(filters.updatedAfter)
  if (updated.window) query.updated_within = updated.window
  if (updated.start) query.updated_after = updated.start
  if (updated.end) query.updated_before = updated.end

  // An applied search always includes sub-cases, so save what was shown.
  if (!filters.includeSubCases && !searchTerm) query.hierarchy = "top_level"
  return query
}

/** Return the order a saved view stores for the filter bar's sort. */
export function sortToView(filters: UseCasesFilters): {
  order_by: CaseSortField
  sort: CaseViewSort
} {
  const searchMode = filters.searchMode ?? "full_text"
  // Relevance needs a full-text term the API will accept, the same term
  // filtersToViewQuery stores.
  if (
    filters.searchOrder === "relevance" &&
    searchMode === "full_text" &&
    getCaseSearchTerm(filters.searchQuery, searchMode)
  ) {
    return { order_by: "relevance", sort: "desc" }
  }
  return { order_by: filters.sortBy.field, sort: filters.sortBy.direction }
}

function viewSort(view: CaseViewRead): CaseSortValue {
  if (!view.order_by || view.order_by === "relevance") {
    return DEFAULT_CASE_SORT
  }
  return { field: view.order_by, direction: view.sort ?? "desc" }
}

function dateFilterFromQuery(
  window: RelativeTimeWindow | null | undefined,
  start: string | null | undefined,
  end: string | null | undefined
): CaseDateFilterValue | null {
  if (window) {
    const preset = windowToPreset(window)
    return preset ? { type: "preset", value: preset } : null
  }
  if (start || end) {
    const from = start ? new Date(start) : undefined
    const to = end ? new Date(end) : undefined
    // The date picker works in whole local days and rounds the end to the
    // end of that day. Keep any other bounds exact, outside the picker.
    // The picker also needs a start date; an end-only range stays outside it.
    if (!from || !isLocalDayStart(from) || (to && !isLocalDayEnd(to))) {
      return null
    }
    return { type: "range", value: { from, to } }
  }
  return DEFAULT_DATE_FILTER
}

function isLocalDayStart(date: Date): boolean {
  return (
    date.getHours() === 0 &&
    date.getMinutes() === 0 &&
    date.getSeconds() === 0 &&
    date.getMilliseconds() === 0
  )
}

function isLocalDayEnd(date: Date): boolean {
  return toEndOfDay(date).getTime() === date.getTime()
}

function extraLabel(field: string, query: CaseQuery_Output): string {
  switch (field) {
    case "created_by":
      return query.created_by?.length === 1 && query.created_by[0] === "me"
        ? "Created by me"
        : "Created by"
    case "filters":
      return "Advanced filters"
    case "created_within":
      return "Created in a custom window"
    case "updated_within":
      return "Updated in a custom window"
    case "created_range":
      return "Created in a fixed range"
    case "updated_range":
      return "Updated in a fixed range"
    case "legacy_search":
      return `Text contains "${query.search_term ?? ""}"`
    case "top_level_search":
      return "Top-level cases only"
    default:
      return field.replaceAll("_", " ")
  }
}

function hasValue(value: unknown): boolean {
  if (value === null || value === undefined) return false
  if (Array.isArray(value)) return value.length > 0
  return true
}

/**
 * Split a view into filter-bar state and the conditions the bar cannot show.
 *
 * Statuses, priorities, and severities load as "include" lists. A relative
 * window that is not one of the filter-bar presets stays in `extraQuery`.
 */
export function viewToFilters(view: CaseViewRead): CaseViewFilters {
  // The server has already removed deleted tags and options from the query
  // it returns for a single view, and lists rules left empty.
  const query: CaseQuery_Output = view.query
  const extraQuery: Record<string, unknown> = {}
  const extraLabels: string[] = []
  for (const [field, value] of Object.entries(query)) {
    if (field === "view_id" || !hasValue(value)) continue
    if (!FILTER_BAR_FIELDS.has(field as keyof CaseQuery_Output)) {
      extraQuery[field] = value
      extraLabels.push(extraLabel(field, query))
    }
  }

  let createdAfter = dateFilterFromQuery(
    query.created_within,
    query.start_time,
    query.end_time
  )
  if (createdAfter === null) {
    extraQuery.created_within = query.created_within
    extraQuery.start_time = query.start_time
    extraQuery.end_time = query.end_time
    extraLabels.push(
      extraLabel(
        query.created_within ? "created_within" : "created_range",
        query
      )
    )
    createdAfter = DEFAULT_DATE_FILTER
  }
  let updatedAfter = dateFilterFromQuery(
    query.updated_within,
    query.updated_after,
    query.updated_before
  )
  if (updatedAfter === null) {
    extraQuery.updated_within = query.updated_within
    extraQuery.updated_after = query.updated_after
    extraQuery.updated_before = query.updated_before
    extraLabels.push(
      extraLabel(
        query.updated_within ? "updated_within" : "updated_range",
        query
      )
    )
    updatedAfter = DEFAULT_DATE_FILTER
  }
  // A search with no mode uses the API's legacy substring matching, which the
  // search box cannot express, so keep it as a view condition.
  const legacySearch = Boolean(query.search_term) && !query.search_mode
  if (legacySearch) {
    extraQuery.search_term = query.search_term
    extraLabels.push(extraLabel("legacy_search", query))
  }
  // A search always shows sub-cases in the filter bar. A view that searches
  // top-level cases only keeps that as a view condition.
  if (query.search_term && query.hierarchy === "top_level") {
    extraQuery.hierarchy = "top_level"
    extraLabels.push(extraLabel("top_level_search", query))
  }

  const dropdownFilters: Record<string, DropdownFilterState> = {}
  for (const entry of query.dropdown ?? []) {
    const parsed = parseDropdownEntry(entry)
    if (!parsed) continue
    const state = dropdownFilters[parsed.definition] ?? {
      values: [],
      mode: "include",
      sortDirection: null,
    }
    state.values.push(parsed.option)
    dropdownFilters[parsed.definition] = state
  }

  const sortBy = viewSort(view)
  const filters: UseCasesFilters = {
    ...DEFAULT_CASES_FILTERS,
    searchQuery: legacySearch ? "" : (query.search_term ?? ""),
    searchMode: query.search_mode === "literal" ? "literal" : "full_text",
    searchOrder: view.order_by === "relevance" ? "relevance" : "column",
    sortBy,
    statusFilter: (query.status ?? []) as CaseStatus[],
    priorityFilter: query.priority ?? [],
    severityFilter: query.severity ?? [],
    assigneeFilter: query.assignee_id ?? [],
    tagFilter: query.tags ?? [],
    dropdownFilters,
    createdAfter,
    updatedAfter,
    includeSubCases: query.hierarchy !== "top_level",
    prioritySortDirection:
      sortBy.field === "priority" ? sortBy.direction : null,
    severitySortDirection:
      sortBy.field === "severity" ? sortBy.direction : null,
    assigneeSortDirection:
      sortBy.field === "assignee" ? sortBy.direction : null,
    tagSortDirection: sortBy.field === "tags" ? sortBy.direction : null,
  }
  return {
    filters,
    extraQuery: extraQuery as CaseViewQuery,
    extraLabels,
    emptyRules: view.empty_rules ?? null,
    savedQuery: query as CaseViewQuery,
  }
}

/**
 * Combine a view's hidden conditions with the filter bar's query.
 *
 * A date the user sets in the filter bar replaces the view's own window.
 */
export function mergeViewQuery(
  extraQuery: CaseViewQuery,
  barQuery: CaseViewQuery
): CaseViewQuery {
  const merged: CaseViewQuery = { ...extraQuery, ...barQuery }
  const groups: (keyof CaseViewQuery)[][] = [
    ["created_within", "start_time", "end_time"],
    ["updated_within", "updated_after", "updated_before"],
    ["search_term", "search_mode", "search_columns"],
  ]
  for (const group of groups) {
    if (group.some((field) => barQuery[field] !== undefined)) {
      // The bar sets this group, so none of the view's own values remain.
      for (const field of group) {
        if (barQuery[field] === undefined) delete merged[field]
      }
    }
  }
  // A parent's sub-cases cannot also be top-level only.
  if (merged.parent_id) delete merged.hierarchy
  return merged
}

/**
 * Explain why the current search cannot be saved, or return null.
 *
 * A search that matches nothing would be saved without its impossible rule
 * and match every case instead, so saving waits until it matches something.
 */
export function viewSaveBlocker(
  filters: UseCasesFilters,
  emptyRules: CaseViewEmptyRules | null | undefined
): string | null {
  const excludesEverything = [
    resolveEnumIncludeFilter(
      filters.statusFilter,
      filters.statusMode,
      ALL_CASE_STATUSES
    ),
    resolveEnumIncludeFilter(
      filters.priorityFilter,
      filters.priorityMode,
      ALL_CASE_PRIORITIES
    ),
    resolveEnumIncludeFilter(
      filters.severityFilter,
      filters.severityMode,
      ALL_CASE_SEVERITIES
    ),
  ].some((resolved) => resolved.matchesNone)
  if (excludesEverything) {
    return "These filters exclude every value, so they match no cases."
  }
  if (emptyRulesMatchNone(emptyRules, filters)) {
    return "Pick new values for the deleted tags or options first."
  }
  return null
}

/** Order-insensitive JSON for comparing two queries. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) {
    const items = value.map(canonical)
    return items.every((item) => typeof item !== "object" || item === null)
      ? [...items].sort()
      : items
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => hasValue(item))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, canonical(item)])
    return Object.fromEntries(entries)
  }
  return value
}

function withoutDefaults(query: CaseViewQuery): CaseViewQuery {
  const { hierarchy, ...rest } = query as CaseQuery_Output
  return hierarchy === "top_level" ? { ...rest, hierarchy } : rest
}

/** Whether two view queries match the same cases. */
export function viewQueriesEqual(a: CaseViewQuery, b: CaseViewQuery): boolean {
  return (
    JSON.stringify(canonical(withoutDefaults(a))) ===
    JSON.stringify(canonical(withoutDefaults(b)))
  )
}

/** Whether two column lists show the same columns in the same order. */
export function columnsEqual(
  a: string[] | null | undefined,
  b: string[] | null | undefined
): boolean {
  const left = a ?? []
  const right = b ?? []
  return (
    left.length === right.length &&
    left.every((column, index) => column === right[index])
  )
}
