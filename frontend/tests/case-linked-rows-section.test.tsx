/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { CaseLinkedTableRead, CaseTableRowRead } from "@/client"
import { CaseLinkedRowsSection } from "@/components/cases/case-linked-rows-section"
import { toast } from "@/components/ui/use-toast"
import { useCaseRowsPagination } from "@/hooks/pagination/use-case-rows-pagination"
import { useCaseLinkedTables, useUnlinkCaseRows } from "@/hooks/use-case-rows"
import { useGetTable } from "@/lib/hooks"

jest.mock("@/hooks/use-case-rows", () => ({
  useCaseLinkedTables: jest.fn(),
  useUnlinkCaseRows: jest.fn(),
}))

jest.mock("@/hooks/pagination/use-case-rows-pagination", () => ({
  useCaseRowsPagination: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useGetTable: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

// AG Grid cannot mount under jsdom; a checkbox per row stands in for it.
jest.mock("@/components/tables/table-rows-grid", () => ({
  TableRowsGrid: ({
    rows,
    selectedRowIds,
    onSelectedRowIdsChange,
  }: {
    rows: readonly { id: string }[]
    selectedRowIds?: ReadonlySet<string>
    onSelectedRowIdsChange?: (rowIds: string[]) => void
  }) => (
    <div data-testid="rows-grid">
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

jest.mock("@/components/tables/ag-grid-pagination", () => ({
  AgGridPagination: () => <div data-testid="pagination" />,
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
const mockUseGetTable = useGetTable as jest.MockedFunction<typeof useGetTable>
const mockToast = toast as jest.MockedFunction<typeof toast>
const mockUnlinkCaseRows = jest.fn()

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

const SUMMARY: CaseLinkedTableRead[] = [
  { table_id: "table-1", table_name: "Alerts", row_count: 2 },
  { table_id: "table-2", table_name: null, row_count: 1 },
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

beforeEach(() => {
  jest.clearAllMocks()
  setLinkedTables(SUMMARY)
  mockUseGetTable.mockImplementation(({ tableId }) => ({
    table: { id: tableId, name: tableId, columns: [] },
    tableIsLoading: false,
    tableError: null,
    refetchTable: jest.fn(),
  }))
  mockUseCaseRowsPagination.mockImplementation(
    ({ tableId, limit }) =>
      ({
        data: LINKS_BY_TABLE[tableId] ?? [],
        isLoading: false,
        error: null,
        refetch: jest.fn(),
        goToNextPage: jest.fn(),
        goToPreviousPage: jest.fn(),
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
      screen.getByRole("button", { name: "Link rows" })
    ).toBeInTheDocument()
    expect(screen.queryByText("No linked table rows")).not.toBeInTheDocument()
    expect(screen.queryByTestId("rows-grid")).not.toBeInTheDocument()
  })

  it("renders skeletons while the summary loads", () => {
    setLinkedTables([], { isLoading: true })
    const { container } = renderSection()

    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3)
    expect(
      screen.queryByRole("button", { name: "Link rows" })
    ).not.toBeInTheDocument()
  })

  it("reports a summary failure", () => {
    setLinkedTables([], { error: new Error("nope") })
    renderSection()

    expect(screen.getByText("Failed to load linked rows")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Link rows" })
    ).not.toBeInTheDocument()
  })

  it("renders one section per linked table with its name and count", () => {
    renderSection()

    expect(screen.getByText("Alerts")).toBeInTheDocument()
    expect(screen.getByText("2 rows")).toBeInTheDocument()
    expect(screen.getByText("Table")).toBeInTheDocument()
    expect(screen.getByText("1 row")).toBeInTheDocument()
    expect(screen.getAllByTestId("rows-grid")).toHaveLength(2)
    expect(mockUseCaseRowsPagination).toHaveBeenCalledWith(
      expect.objectContaining({
        caseId: "case-1",
        tableId: "table-1",
        workspaceId: "ws-1",
        limit: 20,
      })
    )
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

  it("opens the dialog without a table from the link row", async () => {
    const user = userEvent.setup()
    renderSection()

    const dialog = screen.getByTestId("link-rows-dialog")
    expect(dialog).toHaveAttribute("data-open", "false")

    await user.click(screen.getByRole("button", { name: "Link rows" }))

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
})
