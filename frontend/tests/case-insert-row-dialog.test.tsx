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

function makeColumn(
  name: string,
  overrides: Partial<TableColumnRead> = {}
): TableColumnRead {
  return {
    id: `col-${name}`,
    name,
    type: "TEXT",
    nullable: true,
    default: null,
    options: null,
    is_index: false,
    ...overrides,
  }
}

// One column the backend insists on, one nullable, one with a default.
const COLUMNS: TableColumnRead[] = [
  makeColumn("title", { nullable: false }),
  makeColumn("note"),
  makeColumn("source", { nullable: false, default: "manual" }),
  makeColumn("score", { type: "INTEGER" }),
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

beforeEach(() => {
  jest.clearAllMocks()
  mockInsertCaseRow.mockResolvedValue({ id: "link-1" })
  mockUseInsertCaseRow.mockReturnValue({
    insertCaseRow: mockInsertCaseRow,
    insertCaseRowIsPending: false,
  })
})

/** The label of a column's field: its name, type badge and any marker. */
function label(name: string): HTMLElement {
  const element = screen.getByText(name, {
    selector: "label > span",
  }).parentElement
  if (!element) throw new Error(`No label for ${name}`)
  return element
}

/**
 * A column's input. The form does not tie its labels to its inputs, so the
 * input is found beside the label instead of through it.
 */
function field(name: string): HTMLInputElement {
  const input = label(name).parentElement?.querySelector("input")
  if (!input) throw new Error(`No input for ${name}`)
  return input
}

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

  it("marks only the columns the backend insists on as required", () => {
    renderDialog()

    expect(screen.getAllByText("(required)")).toHaveLength(1)
    expect(label("title")).toHaveTextContent("(required)")
    expect(label("note")).not.toHaveTextContent("(required)")
    expect(label("source")).not.toHaveTextContent("(required)")
  })

  it("inserts the row for the case and closes", async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()

    await user.type(field("title"), "Suspicious login")
    await user.type(field("note"), "from the SIEM")
    await user.type(field("source"), "siem")
    await user.type(field("score"), "42")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    await waitFor(() => {
      expect(mockInsertCaseRow).toHaveBeenCalledWith({
        tableId: "table-1",
        data: {
          title: "Suspicious login",
          note: "from the SIEM",
          source: "siem",
          score: 42,
        },
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

  it("leaves blank optional and defaulted columns out of the row", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.type(field("title"), "Suspicious login")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    await waitFor(() => {
      expect(mockInsertCaseRow).toHaveBeenCalledTimes(1)
    })
    // Exactly the one key: no "", null or [] stands in for the blanks.
    expect(mockInsertCaseRow.mock.calls[0][0]).toStrictEqual({
      tableId: "table-1",
      data: { title: "Suspicious login" },
    })
  })

  it("blocks a blank required column", async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()

    await user.type(field("note"), "no title given")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    expect(await screen.findByText("title is required")).toBeInTheDocument()
    expect(mockInsertCaseRow).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("still validates a value typed into an optional column", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.type(field("title"), "Suspicious login")
    await user.type(field("score"), "4.5")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    expect(
      await screen.findByText("score must be an integer")
    ).toBeInTheDocument()
    expect(mockInsertCaseRow).not.toHaveBeenCalled()
  })

  it("stays open when the insert fails", async () => {
    const user = userEvent.setup()
    // The form logs the rejection it swallows.
    const consoleError = jest
      .spyOn(console, "error")
      .mockImplementation(() => undefined)
    mockInsertCaseRow.mockRejectedValueOnce(new Error("Forbidden"))
    const { onOpenChange } = renderDialog()

    await user.type(field("title"), "Suspicious login")
    await user.click(screen.getByRole("button", { name: "Add row" }))

    await waitFor(() => {
      expect(mockInsertCaseRow).toHaveBeenCalled()
    })
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(mockToast).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })
})
