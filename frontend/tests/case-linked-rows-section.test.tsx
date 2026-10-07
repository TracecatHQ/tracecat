/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  ApiError,
  CaseLinkedTableRead,
  CaseTableRowRead,
  TableColumnRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseLinkedRowsSection } from "@/components/cases/case-linked-rows-section"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import {
  CaseRowsUnlinkError,
  useCaseLinkedTables,
  useUnlinkCaseRows,
  useUpdateCaseRow,
} from "@/hooks/use-case-rows"

// Only the hooks are stubbed: the section branches on the real
// CaseRowsUnlinkError.
jest.mock("@/hooks/use-case-rows", () => ({
  ...jest.requireActual("@/hooks/use-case-rows"),
  useCaseLinkedTables: jest.fn(),
  useUnlinkCaseRows: jest.fn(),
  useUpdateCaseRow: jest.fn(),
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

/** Every `rows` prop the mocked grid received, per table, in render order. */
const mockRowsByTable = new Map<string, unknown[]>()

// AG Grid cannot mount under jsdom; a checkbox per row stands in for it, and
// an edit button per row commits a cell edit the way the grid would.
jest.mock("@/components/tables/table-rows-grid", () => ({
  TableRowsGrid: ({
    columns,
    rows,
    tableId,
    selectable,
    selectedRowIds,
    onSelectedRowIdsChange,
    cellPanel,
    onCellValueChange,
    isRowEditable,
    autoHeight,
    sizeColumnsToContent,
  }: {
    columns: readonly { name: string }[]
    rows: readonly { id: string }[]
    tableId: string
    selectable?: boolean
    selectedRowIds?: ReadonlySet<string>
    onSelectedRowIdsChange?: (rowIds: string[]) => void
    cellPanel?: boolean
    onCellValueChange?: (change: {
      rowId: string
      column: string
      value: unknown
    }) => void
    isRowEditable?: (row: { id: string }) => boolean
    autoHeight?: boolean
    sizeColumnsToContent?: boolean
  }) => {
    mockRowsByTable.set(tableId, [
      ...(mockRowsByTable.get(tableId) ?? []),
      rows,
    ])
    return (
      <div
        data-testid="rows-grid"
        data-selectable={String(Boolean(selectable))}
        data-cell-panel={String(Boolean(cellPanel))}
        data-editable={String(Boolean(onCellValueChange))}
        data-auto-height={String(Boolean(autoHeight))}
        data-content-sized={String(Boolean(sizeColumnsToContent))}
        data-columns={columns.map((column) => column.name).join(",")}
      >
        {rows.map((row) => (
          <div key={row.id}>
            <input
              type="checkbox"
              data-testid={`row-${row.id}`}
              data-row-editable={String(isRowEditable?.(row) ?? true)}
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
            {onCellValueChange && (isRowEditable?.(row) ?? true) && (
              <button
                type="button"
                onClick={() =>
                  onCellValueChange({
                    rowId: row.id,
                    column: "name",
                    value: "edited",
                  })
                }
              >
                Edit {row.id}
              </button>
            )}
          </div>
        ))}
      </div>
    )
  },
}))

// The real pagination bar carries a Radix Select; one button exercising the
// page-size callback is all the expanded dialog's tests need.
jest.mock("@/components/tables/ag-grid-pagination", () => ({
  AgGridPagination: ({
    pageSize,
    onPageSizeChange,
  }: {
    pageSize: number
    onPageSizeChange: (pageSize: number) => void
  }) => (
    <button
      type="button"
      data-page-size={pageSize}
      onClick={() => onPageSizeChange(50)}
    >
      Set page size 50
    </button>
  ),
}))

// The cell panel body pulls in the rich-text and JSON editors; the sheet that
// hosts it stays closed in these tests.
jest.mock("@/components/tables/table-side-panel", () => ({
  TableSidePanelContent: () => null,
}))

// The row form is covered through its own wrapper; a marker records each
// table's dialog and whether it is open.
jest.mock("@/components/cases/case-insert-row-dialog", () => ({
  CaseInsertRowDialog: ({
    open,
    tableId,
    columns,
  }: {
    open: boolean
    tableId: string
    columns: readonly { name: string }[]
  }) => (
    <div
      data-testid={`insert-row-dialog-${tableId}`}
      data-open={String(open)}
      data-columns={columns.map((column) => column.name).join(",")}
    />
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
const mockUseUpdateCaseRow = useUpdateCaseRow as jest.MockedFunction<
  typeof useUpdateCaseRow
>
const mockUpdateCaseRow = jest.fn()
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

/** Stands in for the `ApiError` a failed rows request would surface. */
const ROWS_ERROR = new Error("nope") as unknown as ApiError

/**
 * The page the hook reports the moment an arrow is clicked: the next page's
 * bounds, no rows, and no total until the request lands.
 */
const PENDING_NEXT_PAGE: PageState = {
  data: [],
  startItem: 21,
  endItem: 20,
  totalEstimate: 0,
  hasPreviousPage: true,
  hasNextPage: false,
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
  // The app mounts one TooltipProvider at its root; the expand button needs it.
  return render(
    <TooltipProvider>
      <CaseLinkedRowsSection caseId="case-1" workspaceId="ws-1" />
    </TooltipProvider>
  )
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
  mockRowsByTable.clear()
  grantScopes("case:update", "table:read", "table:create", "table:update")
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
  mockUpdateCaseRow.mockResolvedValue(undefined)
  mockUseUpdateCaseRow.mockReturnValue({
    updateCaseRow: mockUpdateCaseRow,
    updateCaseRowIsPending: false,
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
      searchTerm: null,
      searchColumn: null,
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

  it("falls back to the summary count when the total estimate is absent", () => {
    setLinkedTables([
      {
        table_id: "table-1",
        table_name: "Alerts",
        row_count: 37,
        columns: [makeColumn("table-1")],
      },
    ])
    setPage("table-1", {
      hasNextPage: true,
      hasPreviousPage: false,
      startItem: 1,
      endItem: 20,
      // The hook coerces a missing estimate to 0, so 0 is the absent case.
      totalEstimate: 0,
    })
    renderSection()

    expect(screen.getByText("1–20 of 37")).toBeInTheDocument()
    expect(screen.queryByText(/of 0/)).not.toBeInTheDocument()
  })

  it("keeps the summary count while a page loads", () => {
    setPage("table-1", { ...PENDING_NEXT_PAGE, isLoading: true })
    renderSection()

    expect(screen.getByText("2 rows")).toBeInTheDocument()
    expect(screen.queryByText(/21–20/)).not.toBeInTheDocument()
    expect(screen.queryByText(/of 0/)).not.toBeInTheDocument()
  })

  it("keeps the summary count when a page fails", () => {
    setPage("table-1", {
      ...PENDING_NEXT_PAGE,
      isLoading: false,
      error: ROWS_ERROR,
    })
    renderSection()

    expect(screen.getByText("2 rows")).toBeInTheDocument()
    expect(screen.queryByText(/21–20/)).not.toBeInTheDocument()
    expect(screen.getByText("Failed to load linked rows.")).toBeInTheDocument()
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

  it("opens the dialog on a section's table from its link button", async () => {
    const user = userEvent.setup()
    renderSection()

    const [firstLinkRows] = screen.getAllByRole("button", { name: "Link rows" })
    await user.click(firstLinkRows)

    const dialog = screen.getByTestId("link-rows-dialog")
    expect(dialog).toHaveAttribute("data-open", "true")
    expect(dialog).toHaveAttribute("data-initial-table-id", "table-1")
    expect(screen.getByTestId("insert-row-dialog-table-1")).toHaveAttribute(
      "data-open",
      "false"
    )
  })

  it("opens a section's row form from its add button, off the summary's columns", async () => {
    const user = userEvent.setup()
    renderSection()

    const [firstAddRow] = screen.getAllByRole("button", { name: "Add row" })
    await user.click(firstAddRow)

    const insertDialog = screen.getByTestId("insert-row-dialog-table-1")
    expect(insertDialog).toHaveAttribute("data-open", "true")
    expect(insertDialog).toHaveAttribute("data-columns", "name")
    expect(screen.getByTestId("link-rows-dialog")).toHaveAttribute(
      "data-open",
      "false"
    )
  })

  it("offers no add button for a table that has been deleted", () => {
    renderSection()

    // table-2 has no name: its source table is gone, its links remain.
    expect(screen.getAllByRole("button", { name: "Add row" })).toHaveLength(1)
    expect(
      screen.queryByTestId("insert-row-dialog-table-2")
    ).not.toBeInTheDocument()
  })

  it("orders the header actions link, then add", () => {
    renderSection()

    const [linkRows] = screen.getAllByRole("button", { name: "Link rows" })
    const [addRow] = screen.getAllByRole("button", { name: "Add row" })
    expect(
      linkRows.compareDocumentPosition(addRow) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it("saves a cell edit through the case row update", async () => {
    const user = userEvent.setup()
    renderSection()

    for (const grid of screen.getAllByTestId("rows-grid")) {
      expect(grid).toHaveAttribute("data-cell-panel", "true")
      expect(grid).toHaveAttribute("data-editable", "true")
    }

    await user.click(screen.getByRole("button", { name: "Edit r1" }))

    await waitFor(() => {
      expect(mockUpdateCaseRow).toHaveBeenCalledWith({
        tableId: "table-1",
        rowId: "r1",
        data: { name: "edited" },
      })
    })
  })

  it("rebuilds the grid rows after a rejected edit", async () => {
    const user = userEvent.setup()
    mockUpdateCaseRow.mockRejectedValueOnce(new Error("Forbidden"))
    renderSection()

    const before = mockRowsByTable.get("table-1")?.at(-1)
    await user.click(screen.getByRole("button", { name: "Edit r1" }))

    // The links did not change, so only fresh row objects can drop the edit.
    await waitFor(() => {
      expect(mockRowsByTable.get("table-1")?.at(-1)).not.toBe(before)
    })
    expect(mockRowsByTable.get("table-1")?.at(-1)).toEqual(before)
  })

  it("never offers a row whose source row is gone for editing", () => {
    LINKS_BY_TABLE["table-2"] = [
      { ...makeLink("table-2", "r3"), is_row_available: false, row_data: null },
    ]
    try {
      renderSection()

      expect(
        screen.getByRole("button", { name: "Edit r1" })
      ).toBeInTheDocument()
      expect(screen.getByTestId("row-r3")).toHaveAttribute(
        "data-row-editable",
        "false"
      )
      expect(
        screen.queryByRole("button", { name: "Edit r3" })
      ).not.toBeInTheDocument()
    } finally {
      LINKS_BY_TABLE["table-2"] = [makeLink("table-2", "r3")]
    }
  })

  it("sizes the inline grids' columns to their content", () => {
    renderSection()

    for (const grid of screen.getAllByTestId("rows-grid")) {
      expect(grid).toHaveAttribute("data-content-sized", "true")
      expect(grid).toHaveAttribute("data-auto-height", "true")
    }
  })

  describe("expanded table dialog", () => {
    async function expandFirstTable(user: ReturnType<typeof userEvent.setup>) {
      const [expand] = screen.getAllByRole("button", { name: "Expand table" })
      await user.click(expand)
      // The link dialog is mocked to a marker, so this is the only dialog.
      return screen.findByRole("dialog")
    }

    it("puts the expand button after Add row and before the page arrows", () => {
      setPage("table-1", { hasNextPage: true, startItem: 1, endItem: 20 })
      renderSection()

      const [addRow] = screen.getAllByRole("button", { name: "Add row" })
      const [expand] = screen.getAllByRole("button", { name: "Expand table" })
      const previous = screen.getByRole("button", { name: "Previous page" })
      expect(
        addRow.compareDocumentPosition(expand) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
      expect(
        expand.compareDocumentPosition(previous) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    })

    it("opens the same table with a grid that fills the dialog", async () => {
      const user = userEvent.setup()
      renderSection()

      const dialog = await expandFirstTable(user)

      expect(dialog).toHaveAccessibleName("Alerts")
      expect(within(dialog).getByText("2 rows")).toBeInTheDocument()
      const grid = within(dialog).getByTestId("rows-grid")
      expect(grid).toHaveAttribute("data-auto-height", "false")
      expect(grid).toHaveAttribute("data-content-sized", "true")
      expect(grid).toHaveAttribute("data-editable", "true")
      expect(grid).toHaveAttribute("data-columns", "name")
      expect(within(dialog).getByTestId("row-r1")).toBeInTheDocument()
      expect(within(dialog).getByTestId("row-r2")).toBeInTheDocument()
    })

    it("offers link and add, but no second expand or header arrows", async () => {
      const user = userEvent.setup()
      setPage("table-1", { hasNextPage: true, startItem: 1, endItem: 20 })
      renderSection()

      const dialog = await expandFirstTable(user)

      expect(
        within(dialog).getByRole("button", { name: "Link rows" })
      ).toBeInTheDocument()
      expect(
        within(dialog).getByRole("button", { name: "Add row" })
      ).toBeInTheDocument()
      expect(
        within(dialog).queryByRole("button", { name: "Expand table" })
      ).not.toBeInTheDocument()
      expect(
        within(dialog).queryByRole("button", { name: "Next page" })
      ).not.toBeInTheDocument()
      expect(
        within(dialog).getByRole("button", { name: "Set page size 50" })
      ).toBeInTheDocument()
    })

    it("pages at the size picked in its pagination bar", async () => {
      const user = userEvent.setup()
      renderSection()

      const dialog = await expandFirstTable(user)
      await user.click(
        within(dialog).getByRole("button", { name: "Set page size 50" })
      )

      expect(mockUseCaseRowsPagination).toHaveBeenCalledWith({
        caseId: "case-1",
        tableId: "table-1",
        workspaceId: "ws-1",
        limit: 50,
        searchTerm: null,
        searchColumn: null,
      })
    })

    it("keeps its own selection and unlinks from it", async () => {
      const user = userEvent.setup()
      renderSection()

      await user.click(screen.getByTestId("row-r1"))
      expect(screen.getByText("1 selected")).toBeInTheDocument()

      const dialog = await expandFirstTable(user)
      // Expanding drops the inline picks; the dialog starts with none.
      expect(screen.queryByText("1 selected")).not.toBeInTheDocument()

      await user.click(within(dialog).getByTestId("row-r2"))
      await user.click(within(dialog).getByRole("button", { name: "Unlink" }))

      await waitFor(() => {
        expect(mockUnlinkCaseRows).toHaveBeenCalledWith({
          tableId: "table-1",
          rowIds: ["r2"],
        })
      })
    })

    it("saves a cell edit made in the dialog", async () => {
      const user = userEvent.setup()
      renderSection()

      const dialog = await expandFirstTable(user)
      await user.click(within(dialog).getByRole("button", { name: "Edit r2" }))

      await waitFor(() => {
        expect(mockUpdateCaseRow).toHaveBeenCalledWith({
          tableId: "table-1",
          rowId: "r2",
          data: { name: "edited" },
        })
      })
    })

    it("opens the link dialog on its table from inside the dialog", async () => {
      const user = userEvent.setup()
      renderSection()

      const dialog = await expandFirstTable(user)
      await user.click(
        within(dialog).getByRole("button", { name: "Link rows" })
      )

      const linkDialog = screen.getByTestId("link-rows-dialog")
      expect(linkDialog).toHaveAttribute("data-open", "true")
      expect(linkDialog).toHaveAttribute("data-initial-table-id", "table-1")
    })

    it("closes on Escape", async () => {
      const user = userEvent.setup()
      renderSection()

      await expandFirstTable(user)
      await user.keyboard("{Escape}")

      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      })
    })

    it("is offered without any scopes, read-only", async () => {
      const user = userEvent.setup()
      grantScopes()
      renderSection()

      const dialog = await expandFirstTable(user)

      const grid = within(dialog).getByTestId("rows-grid")
      expect(grid).toHaveAttribute("data-editable", "false")
      expect(grid).toHaveAttribute("data-selectable", "false")
      expect(
        within(dialog).queryByRole("button", { name: "Link rows" })
      ).not.toBeInTheDocument()
      expect(
        within(dialog).queryByRole("button", { name: "Add row" })
      ).not.toBeInTheDocument()
    })
  })

  describe("without table:update", () => {
    beforeEach(() => {
      grantScopes("case:update", "table:read", "table:create")
    })

    it("keeps the cells read-only but still opens values in the panel", () => {
      renderSection()

      expect(mockUseScopeCheck).toHaveBeenCalledWith("table:update")
      for (const grid of screen.getAllByTestId("rows-grid")) {
        expect(grid).toHaveAttribute("data-editable", "false")
        expect(grid).toHaveAttribute("data-cell-panel", "true")
      }
      expect(
        screen.queryByRole("button", { name: "Edit r1" })
      ).not.toBeInTheDocument()
      expect(screen.getAllByRole("button", { name: "Add row" })).toHaveLength(1)
    })
  })

  describe("without table:create", () => {
    beforeEach(() => {
      grantScopes("case:update", "table:read", "table:update")
    })

    it("drops the add button and keeps linking and editing", () => {
      renderSection()

      expect(mockUseScopeCheck).toHaveBeenCalledWith(
        "case:update",
        ["table:create"],
        { all: true }
      )
      expect(
        screen.queryByRole("button", { name: "Add row" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByTestId("insert-row-dialog-table-1")
      ).not.toBeInTheDocument()
      expect(screen.getAllByRole("button", { name: "Link rows" })).toHaveLength(
        2
      )
      for (const grid of screen.getAllByTestId("rows-grid")) {
        expect(grid).toHaveAttribute("data-editable", "true")
      }
    })
  })

  describe("without case:update", () => {
    beforeEach(() => {
      grantScopes("table:read", "table:create")
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
        screen.queryByRole("button", { name: "Link rows" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Add row" })
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
        screen.queryByRole("button", { name: "Link rows" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Add row" })
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
        screen.queryByRole("button", { name: "Link rows" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Add row" })
      ).not.toBeInTheDocument()
      for (const grid of screen.getAllByTestId("rows-grid")) {
        expect(grid).toHaveAttribute("data-editable", "false")
      }
    })
  })
})
