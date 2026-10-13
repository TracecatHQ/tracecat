import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type CaseReadMinimal,
  type CasesSearchCasesResponse,
  casesSearchCaseAggregates,
  casesSearchCases,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { CasesLayout } from "@/components/cases/cases-layout"
import { useCases } from "@/hooks/use-cases"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

const mockPush = jest.fn()
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))
jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesSearchCaseAggregates: jest.fn(),
}))
jest.mock("@/lib/hooks", () => ({
  useBatchDeleteCases: () => ({ batchDeleteCases: jest.fn() }),
  useBatchUpdateCases: () => ({ batchUpdateCases: jest.fn() }),
  useBatchChangeCaseParent: () => ({ batchChangeCaseParent: jest.fn() }),
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => true }),
}))
jest.mock("@/components/cases/case-selection-context", () => {
  const selection = { updateSelection: jest.fn(), resetSelection: jest.fn() }
  return { useCaseSelection: () => selection }
})
jest.mock("@/components/cases/case-panel-selectors", () => ({
  UNASSIGNED: "unassigned",
}))
jest.mock("@/components/cases/delete-case-dialog", () => ({
  DeleteCaseAlertDialog: ({ children }: { children: ReactNode }) => children,
}))
jest.mock("@/components/cases/case-side-panel", () => ({
  CaseSidePanel: () => null,
}))
jest.mock("@/components/cases/cases-accordion", () => ({
  orderCasesForDisplay: (cases: CaseReadMinimal[]) => cases,
  CasesAccordion: ({
    cases,
    onSelect,
  }: {
    cases: CaseReadMinimal[]
    onSelect: (id: string) => void
  }) => (
    <div>
      {cases.map((caseData) => (
        <button
          type="button"
          key={caseData.id}
          onClick={() => onSelect(caseData.id)}
        >
          {caseData.short_id}
        </button>
      ))}
    </div>
  ),
}))

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
const CASE: CaseReadMinimal = {
  id: "00000000-0000-4000-8000-000000000042",
  short_id: "CASE-0042",
  created_at: "2020-01-01T00:00:00Z",
  updated_at: "2020-01-01T00:00:00Z",
  summary: "Synthetic older case",
  status: "closed",
  priority: "low",
  severity: "low",
  dropdown_values: [],
  num_sub_cases: 0,
}

function Harness() {
  const search = useCases({ autoRefresh: false })
  return (
    <CasesLayout
      cases={search.cases}
      isLoading={search.isLoading}
      error={search.error}
      filters={search.filters}
      appliedSearchQuery={search.appliedSearchQuery}
      effectiveHierarchy={search.effectiveHierarchy}
      isSearchPending={search.isSearchPending}
      searchScope={search.searchScope}
      onSearchScopeChange={search.setSearchScope}
      onSearchChange={search.setSearchQuery}
      onSortByChange={search.setSortBy}
      onStatusChange={search.setStatusFilter}
      onStatusModeChange={search.setStatusMode}
      onPriorityChange={search.setPriorityFilter}
      onPriorityModeChange={search.setPriorityMode}
      onPrioritySortDirectionChange={search.setPrioritySortDirection}
      onSeverityChange={search.setSeverityFilter}
      onSeverityModeChange={search.setSeverityMode}
      onSeveritySortDirectionChange={search.setSeveritySortDirection}
      onAssigneeChange={search.setAssigneeFilter}
      onAssigneeModeChange={search.setAssigneeMode}
      onAssigneeSortDirectionChange={search.setAssigneeSortDirection}
      onTagChange={search.setTagFilter}
      onTagModeChange={search.setTagMode}
      onTagSortDirectionChange={search.setTagSortDirection}
      onUpdatedAfterChange={search.setUpdatedAfter}
      onCreatedAfterChange={search.setCreatedAfter}
      onIncludeSubCasesChange={search.setIncludeSubCases}
      onDropdownFilterChange={search.setDropdownFilter}
      onDropdownModeChange={search.setDropdownMode}
      onDropdownSortDirectionChange={search.setDropdownSortDirection}
      totalFilteredCaseEstimate={search.totalFilteredCaseEstimate}
      stageCounts={search.stageCounts}
      isCountsLoading={search.isCountsLoading}
      isCountsFetching={search.isCountsFetching}
      hasNextPage={search.hasNextPage}
      isFetchingNextPage={search.isFetchingNextPage}
      onLoadMore={search.fetchNextPage}
    />
  )
}

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceIdProvider workspaceId={WORKSPACE_ID}>
        <Harness />
      </WorkspaceIdProvider>
    </QueryClientProvider>
  )
}

function mockCasesResponse(response: CasesSearchCasesResponse) {
  jest
    .mocked(casesSearchCases)
    .mockImplementation(
      () => new CancelablePromise((resolve) => resolve(response))
    )
}

describe("case search scope UI", () => {
  beforeEach(() => {
    window.localStorage.clear()
    mockPush.mockReset()
    jest.mocked(casesSearchCases).mockReset()
    jest.mocked(casesSearchCases).mockImplementation((request) => {
      const items = request.startTime ? [] : [CASE]
      return new CancelablePromise((resolve) =>
        resolve({
          items,
          has_more: false,
          total_estimate: items.length,
        })
      )
    })
    jest.mocked(casesSearchCaseAggregates).mockImplementation(
      () =>
        new CancelablePromise((resolve) =>
          resolve({
            total: 0,
            status_groups: {
              new: 0,
              in_progress: 0,
              on_hold: 0,
              resolved: 0,
              closed: 0,
              other: 0,
              unknown: 0,
            },
          })
        )
    )
  })

  it("shows the default date and hierarchy constraints and text-search hierarchy", async () => {
    setup()
    expect(screen.getByRole("status")).toHaveTextContent(
      "Current view · Created in last 30 days · Updated: any time · Top-level cases only"
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "synthetic" },
    })
    expect(screen.getByRole("status")).toHaveTextContent("Updating search…")
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Includes sub-cases")
    )
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("switch")).toBeDisabled()
    await screen.findByText("No cases match the current view.")
  })

  it("expands an empty current-view search and navigates to an older case", async () => {
    const user = userEvent.setup()
    setup()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "synthetic" },
    })
    await user.click(
      await screen.findByRole("button", { name: "Search all cases" })
    )
    expect(screen.getByRole("status")).toHaveTextContent(
      "All cases in this workspace · Any date · Includes sub-cases · View filters paused"
    )
    expect(
      screen.getByRole("group", { name: "Current view filters" })
    ).toBeDisabled()
    await user.click(await screen.findByRole("button", { name: "CASE-0042" }))
    expect(mockPush).toHaveBeenCalledWith(
      `/workspaces/${WORKSPACE_ID}/cases/${CASE.id}`
    )
  })

  it("offers all cases when only the default date filter hides older cases", async () => {
    const user = userEvent.setup()
    setup()
    await user.click(
      await screen.findByRole("button", { name: "Search all cases" })
    )
    await screen.findByRole("button", { name: "CASE-0042" })
    expect(screen.getByRole("textbox", { name: "Search cases" })).toHaveValue(
      ""
    )
    expect(screen.getByRole("status")).toHaveTextContent("Any date")
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({ hierarchy: "all" })
    )
    expect(
      screen.getByRole("switch", { name: "Including sub-cases" })
    ).toHaveAttribute("aria-checked", "true")
    expect(
      screen.getByRole("switch", { name: "Including sub-cases" })
    ).toBeDisabled()
    fireEvent.keyDown(
      screen.getByRole("combobox", { name: "Case search scope" }),
      { key: "ArrowDown" }
    )
    await user.click(
      await screen.findByRole("option", { name: "Current view" })
    )
    await screen.findByText("No cases match the current view.")
    expect(casesSearchCases).toHaveBeenLastCalledWith(
      expect.objectContaining({ hierarchy: "top_level" })
    )
    expect(
      screen.getByRole("switch", { name: "Top-level cases only" })
    ).toHaveAttribute("aria-checked", "false")
    expect(
      screen.getByRole("switch", { name: "Top-level cases only" })
    ).toBeEnabled()
  })

  it("makes automatic exact-ID lookup and its filter bypass visible", async () => {
    setup()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "CASE-0042" },
    })
    expect(screen.getByRole("textbox", { name: "Search cases" })).toHaveValue(
      "CASE-0042"
    )
    expect(screen.getByRole("status")).toHaveTextContent("Current view")
    expect(screen.getByRole("status")).toHaveTextContent("Updating search…")
    expect(screen.getByRole("status")).not.toHaveTextContent(
      "Exact case ID lookup"
    )
    await screen.findByRole("button", { name: "CASE-0042" })
    expect(screen.getByRole("status")).toHaveTextContent(
      "Exact case ID lookup · All cases in this workspace"
    )
    expect(
      screen.getByRole("combobox", { name: "Case search scope" })
    ).toHaveTextContent("All cases")
    expect(screen.getByText("1 case")).toBeInTheDocument()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "synthetic" },
    })
    expect(screen.getByRole("status")).toHaveTextContent("Exact case ID lookup")
    expect(screen.getByRole("status")).toHaveTextContent("Updating search…")
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Current view")
    )
    expect(screen.getByRole("status")).not.toHaveTextContent(
      "Exact case ID lookup"
    )
  })

  it("lets an analyst explicitly keep a complete ID within the current view", async () => {
    const user = userEvent.setup()
    setup()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "CASE-0042" },
    })
    await screen.findByRole("button", { name: "CASE-0042" })
    fireEvent.keyDown(
      screen.getByRole("combobox", { name: "Case search scope" }),
      {
        key: "ArrowDown",
      }
    )
    await user.click(
      await screen.findByRole("option", { name: "Current view" })
    )
    await screen.findByText("No cases match the current view.")
    expect(screen.getByRole("status")).toHaveTextContent(
      "Created in last 30 days"
    )
    expect(
      screen.getByRole("group", { name: "Current view filters" })
    ).not.toBeDisabled()
    expect(
      screen.getByRole("button", { name: "Search all cases" })
    ).toBeInTheDocument()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "" },
    })
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: CASE.short_id },
    })
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          searchTerm: CASE.short_id,
          startTime: expect.any(String),
        })
      )
    )
    expect(
      screen.getByRole("combobox", { name: "Case search scope" })
    ).toHaveTextContent("Current view")
  })

  it("states an unavailable exact ID is absent from this workspace", async () => {
    mockCasesResponse({ items: [], has_more: false })
    setup()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: CASE.id },
    })
    await screen.findByText("No matching cases in this workspace.")
    expect(
      screen.queryByRole("button", { name: "Search all cases" })
    ).not.toBeInTheDocument()
  })

  it.each([
    ["CASE-9999999999", "Short ID number cannot exceed 2147483647"],
    ["CASE-12345678901", "Short ID must match CASE-<number> or <number>"],
  ])(
    "shows API validation for %s instead of a filtered text search",
    async (reference, detail) => {
      jest.mocked(casesSearchCases).mockImplementation(
        (request) =>
          new CancelablePromise((_resolve, reject) =>
            request.shortId
              ? reject(
                  Object.assign(new Error("Bad Request"), {
                    status: 400,
                    body: {
                      detail,
                    },
                  })
                )
              : _resolve({ items: [], has_more: false })
          )
      )
      setup()
      fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
        target: { value: reference },
      })
      await screen.findByText(`Failed to load cases: ${detail}`)
      expect(screen.getByRole("status")).toHaveTextContent(
        "Exact case ID lookup"
      )
      expect(casesSearchCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          shortId: reference,
          searchTerm: undefined,
          hierarchy: "all",
        })
      )
    }
  )

  it("preserves manual all-cases scope on clear until Reset is used", async () => {
    const user = userEvent.setup()
    setup()
    await user.click(
      await screen.findByRole("button", { name: "Search all cases" })
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "synthetic" },
    })
    await waitFor(() =>
      expect(casesSearchCases).toHaveBeenCalledWith(
        expect.objectContaining({
          searchTerm: "synthetic",
          hierarchy: "all",
        })
      )
    )
    expect(
      jest.mocked(casesSearchCases).mock.calls.at(-1)?.[0]
    ).not.toHaveProperty("startTime")
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "" },
    })
    expect(
      screen.getByRole("combobox", { name: "Case search scope" })
    ).toHaveTextContent("All cases")
    expect(screen.getByRole("status")).toHaveTextContent("Any date")
    expect(
      screen.getByRole("group", { name: "Current view filters" })
    ).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "Reset" }))
    expect(
      screen.getByRole("combobox", { name: "Case search scope" })
    ).toHaveTextContent("Current view")
  })

  it("resets an explicit scope to automatic lookup behavior", async () => {
    const user = userEvent.setup()
    setup()
    await user.click(
      await screen.findByRole("button", { name: "Search all cases" })
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "synthetic" },
    })
    await user.click(screen.getByRole("button", { name: "Reset" }))
    expect(
      screen.getByRole("combobox", { name: "Case search scope" })
    ).toHaveTextContent("Current view")
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: CASE.short_id },
    })
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Exact case ID lookup"
      )
    )
  })

  it("clears the lookup and restores the saved view constraints", async () => {
    setup()
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: CASE.id },
    })
    await screen.findByRole("button", { name: "CASE-0042" })
    await act(async () => {
      fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
        target: { value: "" },
      })
    })
    expect(screen.getByRole("status")).toHaveTextContent("Current view")
    expect(screen.getByRole("status")).toHaveTextContent(
      "Created in last 30 days"
    )
    expect(
      screen.getByRole("group", { name: "Current view filters" })
    ).not.toBeDisabled()
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "CASE-0042" })
      ).not.toBeInTheDocument()
    )
  })
})
