import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type CaseReadMinimal,
  type CasesSearchCasesResponse,
  casesQueryCaseAggregates,
  casesQueryCases,
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
  casesQueryCases: jest.fn(),
  casesQueryCaseAggregates: jest.fn(),
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
  // Mirrors the real dialog, which renders children beside its content.
  DeleteCaseAlertDialog: ({ children }: { children: ReactNode }) => (
    <>
      {children}
      <div data-testid="delete-case-dialog" />
    </>
  ),
}))
jest.mock("@/components/cases/case-side-panel", () => ({
  CaseSidePanel: () => null,
}))
jest.mock("@/components/cases/case-item", () => ({
  CaseItem: ({
    caseData,
    onClick,
  }: {
    caseData: CaseReadMinimal
    onClick: () => void
  }) => (
    <button type="button" onClick={onClick}>
      {caseData.short_id}
    </button>
  ),
}))
jest.mock("@/components/cases/cases-accordion", () => ({
  orderCasesForDisplay: (cases: CaseReadMinimal[]) => cases,
  CasesAccordion: ({
    cases,
    onSelect,
    stageCounts,
  }: {
    cases: CaseReadMinimal[]
    onSelect: (id: string) => void
    stageCounts: { closed: number } | null
  }) => (
    <div data-testid="ordinary-results" data-closed-count={stageCounts?.closed}>
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

function Harness({
  lexicalControls = false,
  hierarchyEnabled = true,
}: {
  lexicalControls?: boolean
  hierarchyEnabled?: boolean
}) {
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
      onSearchChange={search.setSearchQuery}
      onSearchModeChange={lexicalControls ? search.setSearchMode : undefined}
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
      onIncludeSubCasesChange={
        hierarchyEnabled ? search.setIncludeSubCases : undefined
      }
      onDropdownFilterChange={search.setDropdownFilter}
      onDropdownModeChange={search.setDropdownMode}
      onDropdownSortDirectionChange={search.setDropdownSortDirection}
      totalFilteredCaseEstimate={search.totalFilteredCaseEstimate}
      stageCounts={search.stageCounts}
      isCountsLoading={search.isCountsLoading}
      isCountsFetching={search.isCountsFetching}
      isCountsError={search.isCountsError}
      hasNextPage={search.hasNextPage}
      isFetchingNextPage={search.isFetchingNextPage}
      onLoadMore={search.fetchNextPage}
    />
  )
}

function setup({ lexicalControls = false, hierarchyEnabled = true } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceIdProvider workspaceId={WORKSPACE_ID}>
        <Harness
          lexicalControls={lexicalControls}
          hierarchyEnabled={hierarchyEnabled}
        />
      </WorkspaceIdProvider>
    </QueryClientProvider>
  )
}

function mockCasesResponse(response: CasesSearchCasesResponse) {
  jest
    .mocked(casesQueryCases)
    .mockImplementation(
      () => new CancelablePromise((resolve) => resolve(response))
    )
}

describe("exact-first case search UI", () => {
  beforeEach(() => {
    window.localStorage.clear()
    jest.clearAllMocks()
    mockCasesResponse({ items: [], has_more: false })
    jest.mocked(casesQueryCaseAggregates).mockImplementation(
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

  it("keeps the view filters visible and continues through an empty page", async () => {
    jest.mocked(casesQueryCases).mockImplementation(
      (request) =>
        new CancelablePromise((resolve) =>
          resolve(
            request.requestBody.cursor
              ? { items: [CASE], has_more: false }
              : {
                  items: [],
                  has_more: true,
                  next_cursor: "synthetic-continuation",
                }
          )
        )
    )
    setup()
    await waitFor(() => expect(casesQueryCases).toHaveBeenCalledTimes(1))
    expect(
      screen.queryByRole("combobox", { name: "Case search scope" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("group", { name: "Current view filters" })
    ).not.toBeDisabled()
    expect(
      screen.queryByText("No cases match the current filters.")
    ).not.toBeInTheDocument()
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more cases" })
    )
    await screen.findByRole("button", { name: "CASE-0042" })
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          cursor: "synthetic-continuation",
        }),
      })
    )
  })

  it("shows dates without unsupported hierarchy labels or controls", async () => {
    setup({ hierarchyEnabled: false })
    await waitFor(() => expect(casesQueryCases).toHaveBeenCalledTimes(1))
    expect(screen.queryByText("Parents only")).not.toBeInTheDocument()
    expect(screen.queryByText("All cases")).not.toBeInTheDocument()
    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
  })

  it("uses one search request for a complete ID and keeps the view constraints", async () => {
    setup()
    await waitFor(() => expect(casesQueryCases).toHaveBeenCalledTimes(1))
    mockCasesResponse({
      items: [{ ...CASE, is_exact_match: true, is_outside_filters: true }],
      has_more: false,
      total_estimate: 0,
    })
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "CASE-0042" },
    })
    await screen.findByRole("region", { name: "Exact match" })
    expect(casesQueryCases).toHaveBeenCalledTimes(2)
    expect(jest.mocked(casesQueryCases).mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          search_term: "CASE-0042",
          promote_exact_match: true,
          start_time: expect.any(String),
        }),
      })
    )
    expect(
      screen.getByText("Exact match · Outside current filters")
    ).toBeInTheDocument()
    expect(
      screen.getByRole("group", { name: "Current view filters" })
    ).not.toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "CASE-0042" }))
    expect(mockPush).toHaveBeenCalledWith(
      `/workspaces/${WORKSPACE_ID}/cases/${CASE.id}`
    )
  })

  it("keeps relationship sorting usable with active view filters", async () => {
    const user = userEvent.setup()
    setup()
    const sortControl = screen.getByRole("combobox", {
      name: "Case sort field",
    })
    expect(sortControl).not.toBeDisabled()
    fireEvent.keyDown(sortControl, { key: "ArrowDown" })
    await user.click(await screen.findByRole("option", { name: /^Assignee/ }))
    expect(sortControl).toHaveTextContent("Assignee")
    await waitFor(() =>
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({
            order_by: "assignee",
            sort: "desc",
          }),
        })
      )
    )
    fireEvent.keyDown(sortControl, { key: "ArrowDown" })
    await user.click(await screen.findByRole("option", { name: /^Tags/ }))
    expect(sortControl).toHaveTextContent("Tags")
    await user.click(screen.getByRole("button", { name: "Sort descending" }))
    await waitFor(() =>
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({
            order_by: "tags",
            sort: "asc",
          }),
        })
      )
    )
    await user.click(screen.getByRole("button", { name: "Reset" }))
    expect(sortControl).toHaveTextContent("Updated")
    await waitFor(() =>
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({
            order_by: "updated_at",
            sort: "desc",
          }),
        })
      )
    )
  })

  it("keeps the search input focused while a new search loads", async () => {
    const user = userEvent.setup()
    setup({ lexicalControls: true })
    const searchInput = screen.getByRole("textbox", { name: "Search cases" })
    await waitFor(() => expect(screen.queryByRole("status")).toBeTruthy())
    await waitFor(() => expect(casesQueryCases).toHaveBeenCalled())
    await user.click(searchInput)
    await user.type(searchInput, "malware")
    await waitFor(() =>
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({ search_term: "malware" }),
        })
      )
    )
    await waitFor(() =>
      expect(
        screen.getByText("No cases match the current filters.")
      ).toBeInTheDocument()
    )
    expect(screen.getByRole("textbox", { name: "Search cases" })).toBe(
      searchInput
    )
    expect(searchInput).toHaveFocus()
  })

  it("restores relevance for the next words search after Reset", async () => {
    const user = userEvent.setup()
    setup({ lexicalControls: true })
    const searchInput = screen.getByRole("textbox", { name: "Search cases" })
    fireEvent.change(searchInput, { target: { value: "synthetic" } })
    const sortControl = screen.getByRole("combobox", {
      name: "Case sort field",
    })
    fireEvent.keyDown(sortControl, { key: "ArrowDown" })
    await user.click(await screen.findByRole("option", { name: /^Tags/ }))
    await waitFor(() =>
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({ order_by: "tags" }),
        })
      )
    )
    await user.click(screen.getByRole("button", { name: "Reset" }))
    expect(searchInput).toHaveValue("")
    fireEvent.change(searchInput, { target: { value: "next words" } })
    await waitFor(() =>
      expect(casesQueryCases).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({
            search_term: "next words",
            search_mode: "full_text",
            order_by: "relevance",
            sort: "desc",
          }),
        })
      )
    )
    expect(sortControl).toHaveTextContent("Relevance")
  })

  it("shows the exact match before ordinary matches without duplicating it", async () => {
    const other = {
      ...CASE,
      id: "00000000-0000-4000-8000-000000000043",
      short_id: "CASE-0043",
    }
    mockCasesResponse({
      items: [{ ...CASE, is_exact_match: true }, other],
      has_more: false,
    })
    setup()
    const exact = await screen.findByRole("region", { name: "Exact match" })
    expect(
      within(exact).getByRole("button", { name: "CASE-0042" })
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId("ordinary-results")).queryByText("CASE-0042")
    ).not.toBeInTheDocument()
    expect(
      screen
        .getAllByRole("button", { name: /CASE-004[23]/ })
        .map((button) => button.textContent)
    ).toEqual(["CASE-0042", "CASE-0043"])
    expect(
      screen.queryByText(/Outside current filters/)
    ).not.toBeInTheDocument()
  })

  it("keeps the promoted result out of ordinary status-group counts", async () => {
    mockCasesResponse({
      items: [
        { ...CASE, is_exact_match: true },
        {
          ...CASE,
          id: "00000000-0000-4000-8000-000000000043",
          short_id: "CASE-0043",
          status: "new",
        },
      ],
      has_more: false,
    })
    jest.mocked(casesQueryCaseAggregates).mockImplementation(
      () =>
        new CancelablePromise((resolve) =>
          resolve({
            total: 2,
            status_groups: {
              new: 1,
              in_progress: 0,
              on_hold: 0,
              resolved: 0,
              closed: 1,
              other: 0,
              unknown: 0,
            },
          })
        )
    )
    setup()
    await screen.findByRole("region", { name: "Exact match" })
    await waitFor(() =>
      expect(screen.getByTestId("ordinary-results")).toHaveAttribute(
        "data-closed-count",
        "0"
      )
    )
    expect(screen.getByText("2 cases")).toBeInTheDocument()
  })

  it("clears the promoted result without changing the saved date constraint", async () => {
    setup()
    await waitFor(() => expect(casesQueryCases).toHaveBeenCalledTimes(1))
    mockCasesResponse({
      items: [{ ...CASE, is_exact_match: true, is_outside_filters: true }],
      has_more: false,
    })
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: CASE.id },
    })
    await screen.findByRole("region", { name: "Exact match" })
    mockCasesResponse({ items: [], has_more: false })
    fireEvent.change(screen.getByRole("textbox", { name: "Search cases" }), {
      target: { value: "" },
    })
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Exact match" })
      ).not.toBeInTheDocument()
    )
    expect(casesQueryCases).toHaveBeenLastCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          start_time: expect.any(String),
        }),
      })
    )
  })
})
