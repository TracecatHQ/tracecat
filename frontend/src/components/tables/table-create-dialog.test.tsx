import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ApiError } from "@/client"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"

const mockCreateTable = jest.fn()

jest.mock("@/lib/hooks", () => ({
  useCreateTable: () => ({
    createTable: mockCreateTable,
    createTableIsPending: false,
  }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-synthetic",
}))

function createApiError(status: number, body: unknown) {
  return new ApiError(
    { method: "POST", url: "/tables" },
    {
      url: "/tables",
      ok: false,
      status,
      statusText: "Synthetic failure",
      body,
    },
    "Synthetic failure"
  )
}

function submitTable() {
  fireEvent.change(screen.getByPlaceholderText("Enter table name..."), {
    target: { value: "qa_synthetic_table" },
  })
  fireEvent.change(screen.getByPlaceholderText("Enter column name..."), {
    target: { value: "body" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Create table" }))
}

describe("CreateTableDialog error feedback", () => {
  beforeEach(() => mockCreateTable.mockReset())

  it("shows permission denial and preserves the draft after a rejected write", async () => {
    mockCreateTable.mockRejectedValue(
      createApiError(403, {
        error: {
          code: "insufficient_scope",
          message: "You don't have permission to perform this action.",
          required_scopes: ["table:create"],
          missing_scopes: ["table:create"],
        },
      })
    )
    const onOpenChange = jest.fn()
    render(<CreateTableDialog open onOpenChange={onOpenChange} />)

    submitTable()

    expect(
      await screen.findByText(
        "You don't have permission to create tables in this workspace."
      )
    ).toHaveAttribute("role", "alert")
    expect(screen.getByPlaceholderText("Enter table name...")).toHaveValue(
      "qa_synthetic_table"
    )
    expect(screen.getByPlaceholderText("Enter column name...")).toHaveValue(
      "body"
    )
    expect(mockCreateTable).toHaveBeenCalledTimes(1)
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("shows a root error for a server failure", async () => {
    mockCreateTable.mockRejectedValue(
      createApiError(500, { detail: "Synthetic internal error" })
    )
    render(<CreateTableDialog open onOpenChange={jest.fn()} />)

    submitTable()

    expect(
      await screen.findByText("Failed to create table. Please try again.")
    ).toHaveAttribute("role", "alert")
  })

  it("keeps duplicate-name feedback attached to the name field", async () => {
    mockCreateTable.mockRejectedValue(
      createApiError(409, { detail: "Table already exists" })
    )
    render(<CreateTableDialog open onOpenChange={jest.fn()} />)

    submitTable()

    expect(
      await screen.findByText("A table with this name already exists")
    ).toBeInTheDocument()
    expect(screen.getAllByRole("alert")).toHaveLength(1)
    expect(screen.getByPlaceholderText("Enter table name...")).toHaveAttribute(
      "aria-invalid",
      "true"
    )
  })

  it("closes the dialog after successful creation", async () => {
    mockCreateTable.mockResolvedValue({})
    const onOpenChange = jest.fn()
    render(<CreateTableDialog open onOpenChange={onOpenChange} />)

    submitTable()

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(screen.getAllByRole("alert")).toHaveLength(1)
  })
})
