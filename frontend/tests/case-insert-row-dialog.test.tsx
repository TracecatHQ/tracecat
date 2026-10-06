/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { TableColumnRead } from "@/client"
import { CaseInsertRowDialog } from "@/components/cases/case-insert-row-dialog"
import { toast } from "@/components/ui/use-toast"
import { useInsertCaseRow } from "@/hooks/use-case-rows"

// The dialog reads its columns from props, so the tables hooks its sibling
// wrapper uses must never be reached.
jest.mock("@/lib/hooks", () => ({
  useGetTable: jest.fn(() => {
    throw new Error("The case row form must not load the table")
  }),
  useInsertRow: jest.fn(() => {
    throw new Error("The case row form must not insert through the tables API")
  }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws-1",
}))

jest.mock("@/hooks/use-case-rows", () => ({
  useInsertCaseRow: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockUseInsertCaseRow = useInsertCaseRow as jest.MockedFunction<
  typeof useInsertCaseRow
>
const mockToast = toast as jest.MockedFunction<typeof toast>
const mockInsertCaseRow = jest.fn()

const COLUMNS: TableColumnRead[] = [
  {
    id: "col-1",
    name: "title",
    type: "TEXT",
    nullable: true,
    default: null,
    options: null,
    is_index: false,
  },
]

function renderDialog(tableName: string | null = "Alerts") {
  const onOpenChange = jest.fn()
  render(
    <CaseInsertRowDialog
      open
      onOpenChange={onOpenChange}
      caseId="case-1"
      workspaceId="ws-1"
      tableId="table-1"
      tableName={tableName}
      columns={COLUMNS}
    />
  )
  return { onOpenChange }
}

let consoleError: jest.SpyInstance

beforeEach(() => {
  jest.clearAllMocks()
  // The form starts with no default values, so React warns when the first
  // keystroke controls an input, and the form logs a rejected submit.
  consoleError = jest
    .spyOn(console, "error")
    .mockImplementation(() => undefined)
  mockInsertCaseRow.mockResolvedValue({ id: "link-1" })
  mockUseInsertCaseRow.mockReturnValue({
    insertCaseRow: mockInsertCaseRow,
    insertCaseRowIsPending: false,
  })
})

afterEach(() => {
  consoleError.mockRestore()
})

describe("CaseInsertRowDialog", () => {
  it("says the row will be linked to the case", () => {
    renderDialog()

    expect(
      screen.getByText(
        'Add a new row to the "Alerts" table and link it to this case.'
      )
    ).toBeInTheDocument()
  })

  it("falls back to a generic description for an unnamed table", () => {
    renderDialog(null)

    expect(
      screen.getByText("Add a new row to this table and link it to this case.")
    ).toBeInTheDocument()
  })

  it("inserts the row for the case and closes", async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()

    await user.type(screen.getByRole("textbox"), "Suspicious login")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    await waitFor(() => {
      expect(mockInsertCaseRow).toHaveBeenCalledWith({
        tableId: "table-1",
        data: { title: "Suspicious login" },
      })
    })
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
    expect(mockToast).toHaveBeenCalledWith({
      title: "Row added",
      description: "Added the row and linked it to this case.",
    })
  })

  it("stays open when the insert fails", async () => {
    const user = userEvent.setup()
    mockInsertCaseRow.mockRejectedValueOnce(new Error("Forbidden"))
    const { onOpenChange } = renderDialog()

    await user.type(screen.getByRole("textbox"), "Suspicious login")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    await waitFor(() => {
      expect(mockInsertCaseRow).toHaveBeenCalled()
    })
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(mockToast).not.toHaveBeenCalled()
  })
})
