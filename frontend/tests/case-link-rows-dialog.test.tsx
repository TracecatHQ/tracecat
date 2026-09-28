/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { TableReadMinimal, TableRowRead } from "@/client"
import { CaseLinkRowsDialog } from "@/components/cases/case-link-rows-dialog"
import { toast } from "@/components/ui/use-toast"
import { useTablesPagination } from "@/hooks/pagination/use-tables-pagination"
import { CaseRowsLinkError, useLinkCaseRows } from "@/hooks/use-case-rows"
import { useGetTable, useListTables } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useListTables: jest.fn(),
  useGetTable: jest.fn(),
}))

jest.mock("@/hooks/pagination/use-tables-pagination", () => ({
  useTablesPagination: jest.fn(),
}))

// Only the hook is stubbed: the dialog branches on the real CaseRowsLinkError.
jest.mock("@/hooks/use-case-rows", () => ({
  ...jest.requireActual("@/hooks/use-case-rows"),
  useLinkCaseRows: jest.fn(),
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

// The real pagination bar carries a second Radix Select; a single button
// exercising the page-size callback is all the dialog tests need.
jest.mock("@/components/tables/ag-grid-pagination", () => ({
  AgGridPagination: ({
    onPageSizeChange,
  }: {
    onPageSizeChange: (pageSize: number) => void
  }) => (
    <button type="button" onClick={() => onPageSizeChange(50)}>
      Set page size 50
    </button>
  ),
}))

const mockUseListTables = useListTables as jest.MockedFunction<
  typeof useListTables
>
const mockUseGetTable = useGetTable as jest.MockedFunction<typeof useGetTable>
const mockUseTablesPagination = useTablesPagination as jest.MockedFunction<
  typeof useTablesPagination
>
const mockUseLinkCaseRows = useLinkCaseRows as jest.MockedFunction<
  typeof useLinkCaseRows
>
const mockToast = toast as jest.MockedFunction<typeof toast>
const mockLinkCaseRows = jest.fn()

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.scrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: () => undefined,
    })
  }
})

function makeTable(id: string, name: string): TableReadMinimal {
  return {
    id,
    name,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

function makeRow(id: string): TableRowRead {
  return {
    id,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

// Deliberately unsorted so the dialog's name sort is observable.
const TABLES = [makeTable("table-b", "Beta"), makeTable("table-a", "Alpha")]
const ROWS_BY_TABLE: Record<string, TableRowRead[]> = {
  "table-a": [makeRow("a1"), makeRow("a2")],
  "table-b": [makeRow("b1"), makeRow("b2")],
}

function pageFor(tableId: string, limit: number) {
  return {
    data: ROWS_BY_TABLE[tableId] ?? [],
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
    pageSize: limit,
    totalItems: 2,
    startItem: 1,
    endItem: 2,
    totalEstimate: 2,
    totalPages: 1,
  } as unknown as ReturnType<typeof useTablesPagination>
}

function renderDialog(
  props: Partial<React.ComponentProps<typeof CaseLinkRowsDialog>> = {}
) {
  const onOpenChange = jest.fn()
  render(
    <CaseLinkRowsDialog
      open
      onOpenChange={onOpenChange}
      caseId="case-1"
      workspaceId="ws-1"
      {...props}
    />
  )
  return { onOpenChange }
}

async function pickTable(
  user: ReturnType<typeof userEvent.setup>,
  name: string
) {
  await user.click(screen.getByRole("combobox", { name: "Table" }))
  await user.click(await screen.findByRole("option", { name }))
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUseListTables.mockReturnValue({
    tables: TABLES,
    tablesIsLoading: false,
    tablesError: null,
  })
  mockUseGetTable.mockImplementation(({ tableId }) => ({
    table: { id: tableId, name: tableId, columns: [] },
    tableIsLoading: false,
    tableError: null,
    refetchTable: jest.fn(),
  }))
  mockUseTablesPagination.mockImplementation(({ tableId, limit }) =>
    pageFor(tableId, limit ?? 20)
  )
  mockLinkCaseRows.mockResolvedValue({ linkedCount: 1, alreadyLinkedCount: 0 })
  mockUseLinkCaseRows.mockReturnValue({
    linkCaseRows: mockLinkCaseRows,
    linkCaseRowsIsPending: false,
  })
})

describe("CaseLinkRowsDialog", () => {
  it("calls no data hooks while closed", () => {
    renderDialog({ open: false })

    expect(mockUseListTables).not.toHaveBeenCalled()
    expect(mockUseGetTable).not.toHaveBeenCalled()
    expect(mockUseTablesPagination).not.toHaveBeenCalled()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("lists the workspace tables sorted by name", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole("combobox", { name: "Table" }))

    const options = await screen.findAllByRole("option")
    expect(options.map((option) => option.textContent)).toEqual([
      "Alpha",
      "Beta",
    ])
  })

  it("defaults to the first table by name", () => {
    renderDialog()

    expect(screen.getByTestId("row-a1")).toBeInTheDocument()
    expect(screen.queryByTestId("row-b1")).not.toBeInTheDocument()
  })

  it("preselects initialTableId", () => {
    renderDialog({ initialTableId: "table-b" })

    expect(screen.getByRole("combobox", { name: "Table" })).toHaveTextContent(
      "Beta"
    )
    expect(screen.getByTestId("row-b1")).toBeInTheDocument()
    expect(screen.queryByTestId("row-a1")).not.toBeInTheDocument()
  })

  it("counts ticked rows in the footer and the add button", async () => {
    const user = userEvent.setup()
    renderDialog()

    expect(screen.getByText("0 selected")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add rows" })).toBeDisabled()

    await user.click(screen.getByTestId("row-a1"))
    expect(screen.getByText("1 selected")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add 1 row" })).toBeEnabled()

    await user.click(screen.getByTestId("row-a2"))
    expect(screen.getByText("2 selected")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add 2 rows" })).toBeEnabled()
  })

  it("keeps picks per table across a switch and links each table", async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()

    await user.click(screen.getByTestId("row-a1"))
    await pickTable(user, "Beta")
    expect(screen.getByTestId("row-b1")).not.toBeChecked()
    await user.click(screen.getByTestId("row-b1"))
    expect(screen.getByText("2 selected across 2 tables")).toBeInTheDocument()

    await pickTable(user, "Alpha")
    expect(screen.getByTestId("row-a1")).toBeChecked()
    expect(screen.getByTestId("row-a2")).not.toBeChecked()

    await user.click(screen.getByRole("button", { name: "Add 2 rows" }))

    await waitFor(() => {
      expect(mockLinkCaseRows).toHaveBeenCalledTimes(2)
    })
    expect(mockLinkCaseRows).toHaveBeenCalledWith({
      tableId: "table-a",
      rowIds: ["a1"],
    })
    expect(mockLinkCaseRows).toHaveBeenCalledWith({
      tableId: "table-b",
      rowIds: ["b1"],
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("clears every pick and disables add", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByTestId("row-a1"))
    await pickTable(user, "Beta")
    await user.click(screen.getByTestId("row-b1"))
    expect(screen.getByRole("button", { name: "Clear" })).toBeEnabled()

    await user.click(screen.getByRole("button", { name: "Clear" }))

    expect(screen.getByText("0 selected")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add rows" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled()
    expect(screen.getByTestId("row-b1")).not.toBeChecked()
    await pickTable(user, "Alpha")
    expect(screen.getByTestId("row-a1")).not.toBeChecked()
  })

  it("toasts the summed result and closes on success", async () => {
    const user = userEvent.setup()
    mockLinkCaseRows.mockResolvedValueOnce({
      linkedCount: 1,
      alreadyLinkedCount: 1,
    })
    const { onOpenChange } = renderDialog()

    await user.click(screen.getByTestId("row-a1"))
    await user.click(screen.getByTestId("row-a2"))
    await user.click(screen.getByRole("button", { name: "Add 2 rows" }))

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
    expect(mockToast).toHaveBeenCalledWith({
      title: "Rows linked",
      description: "Linked 1 row to this case. 1 was already linked.",
    })
  })

  it("stays open with picks intact and toasts the API detail on failure", async () => {
    const user = userEvent.setup()
    const failure = Object.assign(new Error("Bad Request"), {
      status: 400,
      body: { detail: "A case can have at most 5000 linked rows" },
    })
    mockLinkCaseRows.mockRejectedValueOnce(failure)
    const { onOpenChange } = renderDialog()

    await user.click(screen.getByTestId("row-a1"))
    await user.click(screen.getByRole("button", { name: "Add 1 row" }))

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({
        title: "Could not link rows",
        description: "A case can have at most 5000 linked rows",
        variant: "destructive",
      })
    })
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(screen.getByTestId("row-a1")).toBeChecked()
    expect(screen.getByText("1 selected")).toBeInTheDocument()
  })

  it("reports partial success and unstages what committed", async () => {
    const user = userEvent.setup()
    const failure = new CaseRowsLinkError({
      linkedCount: 0,
      alreadyLinkedCount: 0,
      committedRowIds: [],
      cause: Object.assign(new Error("Bad Request"), {
        status: 400,
        body: { detail: "A case can have at most 5000 linked rows" },
      }),
    })
    mockLinkCaseRows
      .mockResolvedValueOnce({ linkedCount: 2, alreadyLinkedCount: 0 })
      .mockRejectedValueOnce(failure)
    const { onOpenChange } = renderDialog()

    await user.click(screen.getByTestId("row-a1"))
    await user.click(screen.getByTestId("row-a2"))
    await pickTable(user, "Beta")
    await user.click(screen.getByTestId("row-b1"))
    await user.click(screen.getByRole("button", { name: "Add 3 rows" }))

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({
        title: "Some rows were not linked",
        description:
          "Linked 2 rows before a request failed. A case can have at most 5000 linked rows",
        variant: "destructive",
      })
    })
    expect(onOpenChange).not.toHaveBeenCalled()
    // table-a committed, so only table-b's pick is left for a retry.
    expect(screen.getByText("1 selected")).toBeInTheDocument()
    expect(screen.getByTestId("row-b1")).toBeChecked()
    await pickTable(user, "Alpha")
    expect(screen.getByTestId("row-a1")).not.toBeChecked()
    expect(screen.getByTestId("row-a2")).not.toBeChecked()
  })

  it("keeps picks when the page size changes", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByTestId("row-a1"))
    await user.click(screen.getByRole("button", { name: "Set page size 50" }))

    await waitFor(() => {
      expect(mockUseTablesPagination).toHaveBeenLastCalledWith(
        expect.objectContaining({ tableId: "table-a", limit: 50 })
      )
    })
    expect(screen.getByTestId("row-a1")).toBeChecked()
    expect(screen.getByText("1 selected")).toBeInTheDocument()
  })
})
