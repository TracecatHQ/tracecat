import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AgentPresetDeleteDialog } from "@/components/agents/agent-preset-delete-dialog"

describe("AgentPresetDeleteDialog", () => {
  it("requires an exact preset name match before enabling delete", async () => {
    const user = userEvent.setup()

    render(
      <AgentPresetDeleteDialog
        open={true}
        onOpenChange={() => {}}
        presetName="Triage agent"
        isDeleting={false}
        onConfirm={() => {}}
      />
    )

    const deleteButton = screen.getByRole("button", { name: "Delete agent" })
    const input = screen.getByPlaceholderText('Type "Triage agent" to confirm')

    expect(deleteButton).toBeDisabled()

    await user.type(input, "Triage")
    expect(deleteButton).toBeDisabled()

    await user.clear(input)
    await user.type(input, "Triage agent")
    expect(deleteButton).toBeEnabled()
  })
})
