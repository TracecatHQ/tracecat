import type { CaseViewRead } from "@/client"
import { DEFAULT_CASES_FILTERS, type UseCasesFilters } from "@/hooks/use-cases"
import {
  columnsEqual,
  filtersToViewQuery,
  mergeViewQuery,
  sortToView,
  viewQueriesEqual,
  viewSaveBlocker,
  viewToFilters,
} from "@/lib/case-views"

function makeView(overrides: Partial<CaseViewRead>): CaseViewRead {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    kind: "custom",
    name: "Synthetic view",
    visibility: "personal",
    query: {},
    can_edit: true,
    ...overrides,
  }
}

const PHISHING_FILTERS: UseCasesFilters = {
  ...DEFAULT_CASES_FILTERS,
  statusFilter: ["new", "in_progress"],
  assigneeFilter: ["me", "__UNASSIGNED__"],
  tagFilter: ["phishing"],
  dropdownFilters: {
    verdict: { values: ["malicious"], mode: "include", sortDirection: null },
  },
  createdAfter: { type: "preset", value: "1m" },
  sortBy: { field: "created_at", direction: "desc" },
  searchOrder: "column",
}

describe("filtersToViewQuery", () => {
  it("stores presets as relative windows the server resolves", () => {
    expect(filtersToViewQuery(PHISHING_FILTERS)).toEqual({
      status: ["new", "in_progress"],
      assignee_id: ["me", "__UNASSIGNED__"],
      tags: ["phishing"],
      dropdown: ["verdict:malicious"],
      created_within: { amount: 1, unit: "month" },
      hierarchy: "top_level",
    })
  })

  it("stores an exclusion as the statuses to include", () => {
    const query = filtersToViewQuery({
      ...DEFAULT_CASES_FILTERS,
      statusFilter: ["resolved", "closed"],
      statusMode: "exclude",
      createdAfter: { type: "preset", value: null },
    })

    expect(query.status).toEqual([
      "unknown",
      "new",
      "in_progress",
      "on_hold",
      "other",
    ])
  })
})

describe("viewToFilters", () => {
  it("round-trips filter-bar state through a saved view", () => {
    const query = filtersToViewQuery(PHISHING_FILTERS)
    const view = makeView({ query, ...sortToView(PHISHING_FILTERS) })

    const { filters, extraQuery, extraLabels } = viewToFilters(view)

    expect(viewQueriesEqual(filtersToViewQuery(filters), query)).toBe(true)
    expect(sortToView(filters)).toEqual(sortToView(PHISHING_FILTERS))
    expect(extraQuery).toEqual({})
    expect(extraLabels).toEqual([])
  })

  it("loads the Assigned to me view with sub-cases and no date filter", () => {
    const { filters } = viewToFilters(
      makeView({
        id: "assigned_to_me",
        kind: "builtin",
        // The built-in's real query: open statuses, no hierarchy (sub-cases).
        query: {
          assignee_id: ["me"],
          status: ["unknown", "new", "in_progress", "on_hold", "other"],
        },
        order_by: "created_at",
        sort: "desc",
      })
    )

    expect(filters.assigneeFilter).toEqual(["me"])
    expect(filters.statusFilter).toEqual([
      "unknown",
      "new",
      "in_progress",
      "on_hold",
      "other",
    ])
    expect(filters.includeSubCases).toBe(true)
    expect(filters.createdAfter).toEqual({ type: "preset", value: null })
    expect(filters.sortBy).toEqual({ field: "created_at", direction: "desc" })
  })

  it("keeps conditions the filter bar cannot show", () => {
    const { extraQuery, extraLabels } = viewToFilters(
      makeView({
        query: {
          created_by: ["me"],
          created_within: { amount: 14, unit: "day" },
          filters: { field: "payload.source", op: "eq", value: "edr" },
        },
      })
    )

    expect(extraQuery).toEqual({
      created_by: ["me"],
      created_within: { amount: 14, unit: "day" },
      filters: { field: "payload.source", op: "eq", value: "edr" },
    })
    expect(extraLabels).toEqual([
      "Created by me",
      "Advanced filters",
      "Created in a custom window",
    ])
  })
})

describe("viewQueriesEqual", () => {
  it("ignores value order, empty fields, and the default hierarchy", () => {
    expect(
      viewQueriesEqual(
        { status: ["new", "closed"], tags: [], hierarchy: "all" },
        { status: ["closed", "new"], search_term: null }
      )
    ).toBe(true)
  })

  it("notices a changed filter", () => {
    expect(
      viewQueriesEqual({ tags: ["phishing"] }, { tags: ["malware"] })
    ).toBe(false)
  })
})

describe("columnsEqual", () => {
  it("treats a missing list as no columns and respects order", () => {
    expect(columnsEqual(null, [])).toBe(true)
    expect(columnsEqual(["a", "b"], ["b", "a"])).toBe(false)
  })
})

describe("deleted tags and options", () => {
  it("uses the server's cleaned query and empty rules", () => {
    // The server returns the query without deleted values, and lists rules
    // that were left empty.
    const view = makeView({
      query: { tags: ["phishing"] },
      stale_refs: [{ kind: "dropdown", value: "verdict:gone" }],
      empty_rules: { tags: false, dropdown_refs: ["verdict"], filters: false },
    })
    const { filters, savedQuery, emptyRules } = viewToFilters(view)

    expect(filters.tagFilter).toEqual(["phishing"])
    expect(filters.dropdownFilters.verdict).toBeUndefined()
    expect(savedQuery).toEqual({ tags: ["phishing"] })
    expect(emptyRules?.dropdown_refs).toEqual(["verdict"])
  })

  it("blocks saving until an emptied rule gets new values", () => {
    const { filters, emptyRules } = viewToFilters(
      makeView({
        query: {},
        stale_refs: [{ kind: "tag", value: "retired" }],
        empty_rules: { tags: true, dropdown_refs: [], filters: false },
      })
    )

    expect(viewSaveBlocker(filters, emptyRules)).toMatch(/deleted tags/)
    expect(
      viewSaveBlocker({ ...filters, tagFilter: ["phishing"] }, emptyRules)
    ).toBeNull()
  })

  it("always blocks saving when the filter tree names something deleted", () => {
    const { filters, emptyRules } = viewToFilters(
      makeView({
        query: {},
        empty_rules: { tags: false, dropdown_refs: [], filters: true },
      })
    )

    expect(viewSaveBlocker(filters, emptyRules)).not.toBeNull()
  })
})

describe("mergeViewQuery search and hierarchy groups", () => {
  it("drops a view's search columns when the bar searches", () => {
    const merged = mergeViewQuery(
      {
        search_term: "phish",
        search_mode: "full_text",
        search_columns: ["summary"],
      },
      { search_term: "invoice", search_mode: "full_text" }
    )

    expect(merged.search_columns).toBeUndefined()
    expect(merged.search_term).toBe("invoice")
  })

  it("does not send top-level-only with a parent", () => {
    const merged = mergeViewQuery(
      { parent_id: "00000000-0000-4000-8000-000000000009" },
      { hierarchy: "top_level" }
    )

    expect(merged.hierarchy).toBeUndefined()
  })
})

describe("mergeViewQuery", () => {
  const custom = { created_within: { amount: 14, unit: "day" as const } }

  it("keeps a view's custom window when the filter bar sets no date", () => {
    expect(mergeViewQuery(custom, { tags: ["phishing"] })).toEqual({
      created_within: { amount: 14, unit: "day" },
      tags: ["phishing"],
    })
  })

  it("lets a filter-bar date replace the view's window", () => {
    const merged = mergeViewQuery(custom, {
      start_time: "2026-11-01T00:00:00.000Z",
    })

    expect(merged.created_within).toBeUndefined()
    expect(merged.start_time).toBe("2026-11-01T00:00:00.000Z")
  })
})

describe("viewSaveBlocker", () => {
  it("blocks saving a search that excludes every status", () => {
    const filters: UseCasesFilters = {
      ...DEFAULT_CASES_FILTERS,
      statusFilter: [
        "unknown",
        "new",
        "in_progress",
        "on_hold",
        "resolved",
        "closed",
        "other",
      ],
      statusMode: "exclude",
    }

    expect(viewSaveBlocker(filters, undefined)).toMatch(/match no cases/)
    expect(viewSaveBlocker(DEFAULT_CASES_FILTERS, undefined)).toBeNull()
  })
})

describe("view conditions the filter bar cannot show exactly", () => {
  it("keeps a search with no mode as legacy substring matching", () => {
    const { filters, extraQuery, extraLabels } = viewToFilters(
      makeView({ query: { search_term: "invoice" } })
    )

    expect(filters.searchQuery).toBe("")
    expect(extraQuery).toEqual({ search_term: "invoice" })
    expect(extraLabels).toEqual(['Text contains "invoice"'])
  })

  it("keeps an end time that is not the end of a local day", () => {
    const start = new Date(2026, 10, 1).toISOString()
    const end = new Date(2026, 10, 12, 10).toISOString()
    const { filters, extraQuery } = viewToFilters(
      makeView({ query: { start_time: start, end_time: end } })
    )

    expect(filters.createdAfter).toEqual({ type: "preset", value: null })
    expect(extraQuery.start_time).toBe(start)
    expect(extraQuery.end_time).toBe(end)
  })

  it("puts whole local days in the date picker", () => {
    const start = new Date(2026, 10, 1)
    const end = new Date(2026, 10, 12, 23, 59, 59, 999)
    const { filters, extraQuery } = viewToFilters(
      makeView({
        query: { start_time: start.toISOString(), end_time: end.toISOString() },
      })
    )

    expect(filters.createdAfter).toEqual({
      type: "range",
      value: { from: start, to: end },
    })
    expect(extraQuery).toEqual({})
  })

  it("lets a filter-bar date replace a view's fixed range", () => {
    const merged = mergeViewQuery(
      { start_time: "2026-11-01T10:00:00.000Z" },
      { created_within: { amount: 1, unit: "week" } }
    )

    expect(merged.start_time).toBeUndefined()
    expect(merged.created_within).toEqual({ amount: 1, unit: "week" })
  })
})

describe("filtersToViewQuery hierarchy", () => {
  it("saves a text search with sub-cases, as it was shown", () => {
    const query = filtersToViewQuery({
      ...DEFAULT_CASES_FILTERS,
      searchQuery: "phishing",
      includeSubCases: false,
    })

    expect(query.hierarchy).toBeUndefined()
  })
})
