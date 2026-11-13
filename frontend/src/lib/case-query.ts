import type { DateRange } from "react-day-picker"
import type {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseViewEmptyRules,
  RelativeTimeWindow,
  SearchMode,
} from "@/client"
import {
  type CaseSortValue,
  DEFAULT_CASE_SORT,
} from "@/components/cases/case-sort"
import type {
  FilterMode,
  SortDirection,
} from "@/components/filters/filter-multi-select"

/*
 * Pure case-query building blocks shared by the cases list (useCases) and
 * saved case views. Nothing here touches React or the network.
 */

/** Every case status, in display order. */
/** The assignee and creator value that means the requesting user. */
export const CURRENT_USER = "me"

export const ALL_CASE_STATUSES: ReadonlyArray<CaseStatus> = [
  "unknown",
  "new",
  "in_progress",
  "on_hold",
  "resolved",
  "closed",
  "other",
]
/** Every case priority, in display order. */
export const ALL_CASE_PRIORITIES: ReadonlyArray<CasePriority> = [
  "unknown",
  "low",
  "medium",
  "high",
  "critical",
  "other",
]
/** Every case severity, in display order. */
export const ALL_CASE_SEVERITIES: ReadonlyArray<CaseSeverity> = [
  "unknown",
  "informational",
  "low",
  "medium",
  "high",
  "critical",
  "fatal",
  "other",
]

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

/**
 * Return the search term the API should receive, or "" for none.
 *
 * Input the API would reject (punctuation-only words, too-short literals) is
 * treated as no search, so typing "-" does not replace the list with an error.
 */
export function getCaseSearchTerm(query: string, mode: SearchMode): string {
  const trimmed = query.trim()
  if (mode === "literal") {
    return trimmed.length >= MIN_LITERAL_SEARCH_LENGTH ? query : ""
  }
  return LETTER_OR_NUMBER.test(trimmed) ? trimmed : ""
}

export interface UseCasesFilters {
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

/** The relative window each date preset means. The server resolves it. */
const PRESET_WINDOWS: Record<
  NonNullable<CaseDatePreset>,
  RelativeTimeWindow
> = {
  "1d": { amount: 1, unit: "day" },
  "3d": { amount: 3, unit: "day" },
  "1w": { amount: 1, unit: "week" },
  "1m": { amount: 1, unit: "month" },
}

/** Return the relative window a date preset means, or null for no preset. */
export function presetToWindow(
  preset: CaseDatePreset
): RelativeTimeWindow | null {
  return preset ? PRESET_WINDOWS[preset] : null
}

/** The last millisecond of `date`'s local day. */
export function toEndOfDay(date: Date): Date {
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

/** The filter bar's state with nothing selected. */
export const DEFAULT_CASES_FILTERS: UseCasesFilters = {
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

/**
 * Turn an include or exclude selection into the list of values to include.
 * `matchesNone` is true when an exclusion leaves nothing to include.
 */
export function resolveEnumIncludeFilter<T extends string>(
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

/**
 * Whether a view rule emptied by deletions still has no values, so the search
 * matches nothing. The server reports these rules in `empty_rules`.
 */
export function emptyRulesMatchNone(
  rules: CaseViewEmptyRules | null | undefined,
  filters: Pick<UseCasesFilters, "tagFilter" | "dropdownFilters">
): boolean {
  if (!rules) return false
  if (rules.filters) return true
  if (rules.tags && filters.tagFilter.length === 0) return true
  return (rules.dropdown_refs ?? []).some(
    (ref) => (filters.dropdownFilters[ref]?.values.length ?? 0) === 0
  )
}

/** Split a `definition_ref:option_ref` dropdown filter, or null if malformed. */
export function parseDropdownEntry(
  entry: string
): { definition: string; option: string } | null {
  const separator = entry.indexOf(":")
  if (separator <= 0 || separator === entry.length - 1) return null
  return {
    definition: entry.slice(0, separator),
    option: entry.slice(separator + 1),
  }
}

/** Join refs into a `definition_ref:option_ref` dropdown filter. */
export function formatDropdownEntry(
  definition: string,
  option: string
): string {
  return `${definition}:${option}`
}
