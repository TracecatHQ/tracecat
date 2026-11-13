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
  const staleView = makeView({
    query: {
      tags: ["phishing", "retired"],
      dropdown: ["verdict:gone"],
    },
    stale_refs: [
      { kind: "tag", value: "retired" },
      { kind: "dropdown", value: "verdict:gone" },
    ],
  })

  it("drops deleted values from the filter bar and the saved query", () => {
    const { filters, savedQuery, staleRules } = viewToFilters(staleView)

    expect(filters.tagFilter).toEqual(["phishing"])
    expect(filters.dropdownFilters.verdict).toBeUndefined()
    expect(savedQuery.tags).toEqual(["phishing"])
    expect(savedQuery.dropdown).toEqual([])
    expect(staleRules).toEqual({ tags: false, dropdownRefs: ["verdict"] })
  })

  it("marks a rule whose values were all deleted, and blocks saving it", () => {
    const { filters, staleRules } = viewToFilters(
      makeView({
        query: { tags: ["retired"] },
        stale_refs: [{ kind: "tag", value: "retired" }],
      })
    )

    expect(staleRules.tags).toBe(true)
    expect(viewSaveBlocker(filters, staleRules)).toMatch(/deleted tags/)
    expect(
      viewSaveBlocker({ ...filters, tagFilter: ["phishing"] }, staleRules)
    ).toBeNull()
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
