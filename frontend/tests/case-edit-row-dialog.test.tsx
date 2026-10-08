/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { TableColumnRead, TableRowRead } from "@/client"
import { CaseEditRowDialog } from "@/components/cases/case-edit-row-dialog"
import { useUpdateCaseRow } from "@/hooks/use-case-rows"

jest.mock("@/lib/hooks", () => ({
  useGetTable: jest.fn(),
  useInsertRow: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws-1",
}))

jest.mock("@/hooks/use-case-rows", () => ({
  useUpdateCaseRow: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockUseUpdateCaseRow = useUpdateCaseRow as jest.MockedFunction<
  typeof useUpdateCaseRow
>
const mockUpdateCaseRow = jest.fn()

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

const COLUMNS: TableColumnRead[] = [
  makeColumn("title", { nullable: false }),
  makeColumn("note"),
  makeColumn("score", { type: "INTEGER" }),
]

const ROW: TableRowRead = {
  id: "row-1",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  title: "Phishing",
  note: "Reported by user",
  score: 7,
}

function field(name: string): HTMLInputElement {
  const input = screen
    .getByText(name, { selector: "label > span" })
    .parentElement?.parentElement?.querySelector("input")
  if (!input) throw new Error(`No input for ${name}`)
  return input
}

function renderDialog() {
  const onOpenChange = jest.fn()
  render(
    <CaseEditRowDialog
      row={ROW}
      onOpenChange={onOpenChange}
      caseId="case-1"
      workspaceId="ws-1"
      tableId="table-1"
      tableName="Alerts"
      columns={COLUMNS}
    />
  )
  return { onOpenChange }
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUpdateCaseRow.mockResolvedValue(undefined)
  mockUseUpdateCaseRow.mockReturnValue({
    updateCaseRow: mockUpdateCaseRow,
    updateCaseRowIsPending: false,
  })
})

describe("CaseEditRowDialog", () => {
  it("fills the form with the row's values", () => {
    renderDialog()

    expect(screen.getByText("Edit row")).toBeInTheDocument()
    expect(field("title")).toHaveValue("Phishing")
    expect(field("note")).toHaveValue("Reported by user")
    expect(field("score")).toHaveValue("7")
  })

  it("submits only changed columns, a cleared one as null", async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()

    await user.clear(field("title"))
    await user.type(field("title"), "Malware")
    await user.clear(field("note"))
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(mockUpdateCaseRow).toHaveBeenCalledWith({
        tableId: "table-1",
        rowId: "row-1",
        data: { title: "Malware", note: null },
      })
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("closes without a request when nothing changed", async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()

    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(mockUpdateCaseRow).not.toHaveBeenCalled()
  })
})
