import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { TooltipProvider } from "@/components/ui/tooltip"

function renderDialog(onConfirm: () => void, onOpenChange = jest.fn()) {
  render(
    <TooltipProvider>
      <ConfirmDestructiveDialog
        open={true}
        onOpenChange={onOpenChange}
        title="Delete workflow"
        description="This cannot be undone."
        identifier={{ label: "Workflow ID", value: "wf_abc123" }}
        confirmPhrase="wf_abc123"
        onConfirm={onConfirm}
      />
    </TooltipProvider>
  )
}

describe("ConfirmDestructiveDialog", () => {
  it("shows the identifier with a copy button", () => {
    renderDialog(jest.fn())
    expect(
      screen.getByTestId("confirm-destructive-identifier")
    ).toHaveTextContent("wf_abc123")
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument()
  })

  it("only confirms once the exact phrase is typed", async () => {
    const user = userEvent.setup()
    const onConfirm = jest.fn()
    renderDialog(onConfirm)

    const deleteButton = screen.getByRole("button", { name: "Delete" })
    const input = screen.getByLabelText("Type the workflow ID to confirm")
    expect(deleteButton).toBeDisabled()

    await user.type(input, "wf_abc12")
    expect(deleteButton).toBeDisabled()
    await user.type(input, "{Enter}")
    expect(onConfirm).not.toHaveBeenCalled()

    await user.type(input, "3")
    expect(deleteButton).toBeEnabled()
    await user.click(deleteButton)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it("asks for a final confirmation when a final step is set", async () => {
    const user = userEvent.setup()
    const onConfirm = jest.fn()
    render(
      <TooltipProvider>
        <ConfirmDestructiveDialog
          open={true}
          onOpenChange={jest.fn()}
          title="Delete folder and contents"
          description="This cannot be undone."
          identifier={{ label: "Folder ID", value: "fld_1" }}
          confirmPhrase="delete folder fld_1"
          confirmLabel="Continue"
          finalStep={{
            title: "Are you sure?",
            description: "You will be deleting 3 workflows.",
            confirmLabel: "Delete 3 workflows",
          }}
          onConfirm={onConfirm}
        />
      </TooltipProvider>
    )

    await user.type(screen.getByRole("textbox"), "delete folder fld_1")
    await user.click(screen.getByRole("button", { name: "Continue" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(
      screen.getByText("You will be deleting 3 workflows.")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus()

    await user.click(screen.getByRole("button", { name: "Delete 3 workflows" }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})
