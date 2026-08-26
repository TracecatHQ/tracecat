/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  CaseLinkedTableRead,
  CaseTableRowRead,
  TableColumnRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseLinkedRowsSection } from "@/components/cases/case-linked-rows-section"
import { toast } from "@/components/ui/use-toast"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import {
  CaseRowsUnlinkError,
  useCaseLinkedTables,
  useUnlinkCaseRows,
} from "@/hooks/use-case-rows"

// Only the hooks are stubbed: the section branches on the real
// CaseRowsUnlinkError.
jest.mock("@/hooks/use-case-rows", () => ({
  ...jest.requireActual("@/hooks/use-case-rows"),
  useCaseLinkedTables: jest.fn(),
  useUnlinkCaseRows: jest.fn(),
}))

jest.mock("@/hooks/pagination/use-case-rows-pagination", () => ({
  useCaseRowsPagination: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

// AG Grid cannot mount under jsdom; a checkbox per row stands in for it.
jest.mock("@/components/tables/table-rows-grid", () => ({
  TableRowsGrid: ({
    columns,
    rows,
    selectable,
    selectedRowIds,
    onSelectedRowIdsChange,
  }: {
    columns: readonly { name: string }[]
    rows: readonly { id: string }[]
    selectable?: boolean
    selectedRowIds?: ReadonlySet<string>
    onSelectedRowIdsChange?: (rowIds: string[]) => void
  }) => (
    <div
      data-testid="rows-grid"
      data-selectable={String(Boolean(selectable))}
      data-columns={columns.map((column) => column.name).join(",")}
    >
      {rows.map((row) => (
        <input
          key={row.id}
          type="checkbox"
          data-testid={`row-${row.id}`}
          checked={selectedRowIds?.has(row.id) ?? false}
          onChange={() => {
            const next = new Set(selectedRowIds ?? [])
            if (next.has(row.id)) {
              next.delete(row.id)
            } else {
              next.add(row.id)
            }
            onSelectedRowIdsChange?.([...next])
          }}
        />
      ))}
    </div>
  ),
}))

// The dialog is covered by its own suite; a marker records how it was opened.
jest.mock("@/components/cases/case-link-rows-dialog", () => ({
  CaseLinkRowsDialog: ({
    open,
    initialTableId,
  }: {
    open: boolean
    initialTableId?: string
  }) => (
    <div
      data-testid="link-rows-dialog"
      data-open={String(open)}
      data-initial-table-id={initialTableId ?? ""}
    />
  ),
}))

const mockUseCaseLinkedTables = useCaseLinkedTables as jest.MockedFunction<
  typeof useCaseLinkedTables
>
const mockUseUnlinkCaseRows = useUnlinkCaseRows as jest.MockedFunction<
  typeof useUnlinkCaseRows
>
const mockUseCaseRowsPagination = useCaseRowsPagination as jest.MockedFunction<
  typeof useCaseRowsPagination
>
const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>
const mockToast = toast as jest.MockedFunction<typeof toast>
const mockUnlinkCaseRows = jest.fn()
const mockGoToNextPage = jest.fn()
const mockGoToPreviousPage = jest.fn()

type PageState = Partial<ReturnType<typeof useCaseRowsPagination>>

const pageOverrides = new Map<string, PageState>()

/** Overrides the page the mocked pagination hook reports for one table. */
function setPage(tableId: string, overrides: PageState) {
  pageOverrides.set(tableId, overrides)
}

function makeLink(tableId: string, rowId: string): CaseTableRowRead {
  return {
    id: `link-${rowId}`,
    case_id: "case-1",
    table_id: tableId,
    table_name: "Alerts",
    row_id: rowId,
    row_data: { name: rowId },
    is_row_available: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

const LINKS_BY_TABLE: Record<string, CaseTableRowRead[]> = {
  "table-1": [makeLink("table-1", "r1"), makeLink("table-1", "r2")],
  "table-2": [makeLink("table-2", "r3")],
}

function makeColumn(tableId: string): TableColumnRead {
  return {
    id: `col-${tableId}`,
    name: "name",
    type: "TEXT",
    nullable: true,
    default: null,
    options: null,
    is_index: false,
  }
}

const SUMMARY: CaseLinkedTableRead[] = [
  {
    table_id: "table-1",
    table_name: "Alerts",
    row_count: 2,
    columns: [makeColumn("table-1")],
  },
  {
    table_id: "table-2",
    table_name: null,
    row_count: 1,
    columns: [makeColumn("table-2")],
  },
]

function setLinkedTables(
  linkedTables: CaseLinkedTableRead[],
  state: { isLoading?: boolean; error?: Error | null } = {}
) {
  mockUseCaseLinkedTables.mockReturnValue({
    linkedTables,
    linkedTablesIsLoading: state.isLoading ?? false,
    linkedTablesError: state.error ?? null,
  } as unknown as ReturnType<typeof useCaseLinkedTables>)
}

function renderSection() {
  return render(<CaseLinkedRowsSection caseId="case-1" workspaceId="ws-1" />)
}

const grantedScopes = new Set<string>()

/** Replaces the granted scopes the mocked `useScopeCheck` answers from. */
function grantScopes(...scopes: string[]) {
  grantedScopes.clear()
  for (const scope of scopes) {
    grantedScopes.add(scope)
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  grantScopes("case:update", "table:read")
  // Mirrors the real hook: `all` requires every scope, otherwise any one.
  mockUseScopeCheck.mockImplementation((scope, scopes, options) => {
    const required = [...(scope ? [scope] : []), ...(scopes ?? [])]
    if (required.length === 0) {
      return true
    }
    if (required.length === 1 || options?.all) {
      return required.every((name) => grantedScopes.has(name))
    }
    return required.some((name) => grantedScopes.has(name))
  })
  setLinkedTables(SUMMARY)
  pageOverrides.clear()
  mockUseCaseRowsPagination.mockImplementation(
    ({ tableId, limit }) =>
      ({
        data: LINKS_BY_TABLE[tableId] ?? [],
        isLoading: false,
        error: null,
        refetch: jest.fn(),
        goToNextPage: mockGoToNextPage,
        goToPreviousPage: mockGoToPreviousPage,
        goToFirstPage: jest.fn(),
        setSorting: jest.fn(),
        sortingState: { orderBy: null, sort: null },
        hasNextPage: false,
        hasPreviousPage: false,
        currentPage: 0,
        pageSize: limit ?? 20,
        totalItems: 1,
        startItem: 1,
        endItem: 1,
        totalEstimate: 1,
        totalPages: 1,
        ...pageOverrides.get(tableId),
      }) as unknown as ReturnType<typeof useCaseRowsPagination>
  )
  mockUnlinkCaseRows.mockResolvedValue({ unlinkedCount: 1 })
  mockUseUnlinkCaseRows.mockReturnValue({
    unlinkCaseRows: mockUnlinkCaseRows,
    unlinkCaseRowsIsPending: false,
  })
})

describe("CaseLinkedRowsSection", () => {
  it("shows only the link row when nothing is linked", () => {
    setLinkedTables([])
    renderSection()

    expect(
      screen.getByRole("button", { name: "Link table" })
    ).toBeInTheDocument()
    expect(screen.queryByText("No linked table rows")).not.toBeInTheDocument()
    expect(screen.queryByTestId("rows-grid")).not.toBeInTheDocument()
  })

  it("renders skeletons while the summary loads", () => {
    setLinkedTables([], { isLoading: true })
    const { container } = renderSection()

    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3)
    expect(
      screen.queryByRole("button", { name: "Link table" })
    ).not.toBeInTheDocument()
  })

  it("reports a summary failure", () => {
    setLinkedTables([], { error: new Error("nope") })
    renderSection()

    expect(screen.getByText("Failed to load linked rows")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Link table" })
    ).not.toBeInTheDocument()
  })

  it("renders one section per linked table with its name and count", () => {
    renderSection()

    expect(screen.getByText("Alerts")).toBeInTheDocument()
    expect(screen.getByText("2 rows")).toBeInTheDocument()
    expect(screen.getByText("Table")).toBeInTheDocument()
    expect(screen.getByText("1 row")).toBeInTheDocument()
    expect(screen.getAllByTestId("rows-grid")).toHaveLength(2)
    expect(
      screen.getByRole("button", { name: "Link table" })
    ).toBeInTheDocument()
    expect(mockUseCaseRowsPagination).toHaveBeenCalledWith({
      caseId: "case-1",
      tableId: "table-1",
      workspaceId: "ws-1",
      limit: 20,
    })
  })

  it("pages at a fixed size, with no rows-per-page control", () => {
    renderSection()

    for (const call of mockUseCaseRowsPagination.mock.calls) {
      expect(call[0]).toMatchObject({ limit: 20 })
    }
    expect(screen.queryByText(/rows per page/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/page 1 of/i)).not.toBeInTheDocument()
  })

  it("hides the page arrows when everything fits on one page", () => {
    renderSection()

    expect(
      screen.queryByRole("button", { name: "Previous page" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Next page" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("2 rows")).toBeInTheDocument()
  })

  it("shows the visible range and pages forward when there is more", async () => {
    const user = userEvent.setup()
    setPage("table-1", {
      hasNextPage: true,
      hasPreviousPage: false,
      startItem: 1,
      endItem: 20,
      totalEstimate: 45,
    })
    renderSection()

    expect(screen.getByText("1–20 of 45")).toBeInTheDocument()
    expect(screen.queryByText("2 rows")).not.toBeInTheDocument()

    const previous = screen.getByRole("button", { name: "Previous page" })
    const next = screen.getByRole("button", { name: "Next page" })
    expect(previous).toBeDisabled()
    expect(next).toBeEnabled()

    await user.click(next)
    expect(mockGoToNextPage).toHaveBeenCalled()
  })

  it("disables both arrows while a page loads", () => {
    setPage("table-1", {
      isLoading: true,
      hasNextPage: true,
      hasPreviousPage: true,
      startItem: 21,
      endItem: 40,
      totalEstimate: 45,
    })
    renderSection()

    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled()
  })

  it("renders the grids off the summary's columns", () => {
    renderSection()

    for (const grid of screen.getAllByTestId("rows-grid")) {
      expect(grid).toHaveAttribute("data-columns", "name")
    }
  })

  it("hands the grid rows keyed by row_id", () => {
    renderSection()

    expect(screen.getByTestId("row-r1")).toBeInTheDocument()
    expect(screen.getByTestId("row-r2")).toBeInTheDocument()
    expect(screen.getByTestId("row-r3")).toBeInTheDocument()
    expect(screen.queryByTestId("row-link-r1")).not.toBeInTheDocument()
  })

  it("unlinks the ticked rows and clears the selection", async () => {
    const user = userEvent.setup()
    renderSection()

    expect(
      screen.queryByRole("button", { name: "Unlink" })
    ).not.toBeInTheDocument()

    await user.click(screen.getByTestId("row-r1"))
    expect(screen.getByText("1 selected")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Unlink" }))

    await waitFor(() => {
      expect(mockUnlinkCaseRows).toHaveBeenCalledWith({
        tableId: "table-1",
        rowIds: ["r1"],
      })
    })
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Unlink" })
      ).not.toBeInTheDocument()
    })
    expect(screen.getByTestId("row-r1")).not.toBeChecked()
    expect(mockToast).toHaveBeenCalledWith({
      title: "Rows unlinked",
      description: "Unlinked 1 row from this case.",
    })
  })

  it("toasts the API detail when unlinking fails", async () => {
    const user = userEvent.setup()
    mockUnlinkCaseRows.mockRejectedValueOnce(
      Object.assign(new Error("Forbidden"), {
        status: 403,
        body: { detail: "Not allowed" },
      })
    )
    renderSection()

    await user.click(screen.getByTestId("row-r1"))
    await user.click(screen.getByRole("button", { name: "Unlink" }))

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({
        title: "Could not unlink rows",
        description: "Not allowed",
        variant: "destructive",
      })
    })
    expect(screen.getByTestId("row-r1")).toBeChecked()
  })

  it("reports partial success and deselects what unlinked", async () => {
    const user = userEvent.setup()
    mockUnlinkCaseRows.mockRejectedValueOnce(
      new CaseRowsUnlinkError({
        unlinkedCount: 1,
        committedRowIds: ["r1"],
        cause: Object.assign(new Error("Forbidden"), {
          status: 403,
          body: { detail: "Not allowed" },
        }),
      })
    )
    renderSection()

    await user.click(screen.getByTestId("row-r1"))
    await user.click(screen.getByTestId("row-r2"))
    await user.click(screen.getByRole("button", { name: "Unlink" }))

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({
        title: "Some rows were not unlinked",
        description: "Unlinked 1 row before a request failed. Not allowed",
        variant: "destructive",
      })
    })
    // r1 committed, so only r2 is left for a retry.
    expect(screen.getByTestId("row-r1")).not.toBeChecked()
    expect(screen.getByTestId("row-r2")).toBeChecked()
    expect(screen.getByText("1 selected")).toBeInTheDocument()
  })

  it("opens the dialog without a table from the link row", async () => {
    const user = userEvent.setup()
    renderSection()

    const dialog = screen.getByTestId("link-rows-dialog")
    expect(dialog).toHaveAttribute("data-open", "false")

    await user.click(screen.getByRole("button", { name: "Link table" }))

    expect(dialog).toHaveAttribute("data-open", "true")
    expect(dialog).toHaveAttribute("data-initial-table-id", "")
  })

  it("opens the dialog on a section's table from its add button", async () => {
    const user = userEvent.setup()
    renderSection()

    const [firstAddRows] = screen.getAllByRole("button", { name: "Add rows" })
    await user.click(firstAddRows)

    const dialog = screen.getByTestId("link-rows-dialog")
    expect(dialog).toHaveAttribute("data-open", "true")
    expect(dialog).toHaveAttribute("data-initial-table-id", "table-1")
  })

  describe("without case:update", () => {
    beforeEach(() => {
      grantScopes("table:read")
    })

    it("checks for the case:update and table:read scopes", () => {
      renderSection()

      expect(mockUseScopeCheck).toHaveBeenCalledWith("case:update")
      expect(mockUseScopeCheck).toHaveBeenCalledWith(
        "case:update",
        ["table:read"],
        { all: true }
      )
    })

    it("leaves the grids read-only and drops every mutation control", () => {
      renderSection()

      const grids = screen.getAllByTestId("rows-grid")
      expect(grids).toHaveLength(2)
      for (const grid of grids) {
        expect(grid).toHaveAttribute("data-selectable", "false")
      }

      expect(screen.getByText("Alerts")).toBeInTheDocument()
      expect(screen.getByText("2 rows")).toBeInTheDocument()
      expect(screen.getByText("Table")).toBeInTheDocument()
      expect(screen.getByText("1 row")).toBeInTheDocument()

      expect(
        screen.queryByRole("button", { name: "Link table" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Add rows" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Unlink" })
      ).not.toBeInTheDocument()
    })

    it("shows a read-only empty state when nothing is linked", () => {
      setLinkedTables([])
      renderSection()

      expect(screen.getByText("No linked rows")).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Link table" })
      ).not.toBeInTheDocument()
    })
  })

  describe("with case:update but without table:read", () => {
    beforeEach(() => {
      grantScopes("case:update")
    })

    it("keeps unlinking but drops the controls that open the link dialog", async () => {
      const user = userEvent.setup()
      renderSection()

      const grids = screen.getAllByTestId("rows-grid")
      expect(grids).toHaveLength(2)
      for (const grid of grids) {
        expect(grid).toHaveAttribute("data-selectable", "true")
      }

      await user.click(screen.getByTestId("row-r1"))
      expect(screen.getByText("1 selected")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Unlink" })).toBeInTheDocument()

      expect(
        screen.queryByRole("button", { name: "Link table" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Add rows" })
      ).not.toBeInTheDocument()
    })

    it("shows a read-only empty state when nothing is linked", () => {
      setLinkedTables([])
      renderSection()

      expect(screen.getByText("No linked rows")).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Link table" })
      ).not.toBeInTheDocument()
    })
  })

  describe("without any scopes", () => {
    beforeEach(() => {
      grantScopes()
    })

    it("still pages, because paging reads nothing new", () => {
      setPage("table-1", {
        hasNextPage: true,
        hasPreviousPage: false,
        startItem: 1,
        endItem: 20,
        totalEstimate: 45,
      })
      renderSection()

      expect(screen.getByText("1–20 of 45")).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Previous page" })
      ).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled()
      expect(
        screen.queryByRole("button", { name: "Add rows" })
      ).not.toBeInTheDocument()
    })
  })
})
