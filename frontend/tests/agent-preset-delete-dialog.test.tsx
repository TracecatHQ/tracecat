import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AgentPresetDeleteDialog } from "@/components/agents/agent-preset-delete-dialog"
import { TooltipProvider } from "@/components/ui/tooltip"

describe("AgentPresetDeleteDialog", () => {
  it("shows the agent slug and requires it before enabling delete", async () => {
    const user = userEvent.setup()

    render(
      <TooltipProvider>
        <AgentPresetDeleteDialog
          open={true}
          onOpenChange={() => {}}
          presetName="Triage agent"
          presetSlug="triage-agent"
          isDeleting={false}
          onConfirm={() => {}}
        />
      </TooltipProvider>
    )

    expect(
      screen.getByTestId("confirm-destructive-identifier")
    ).toHaveTextContent("triage-agent")
    const deleteButton = screen.getByRole("button", { name: "Delete" })
    const input = screen.getByLabelText("Type the agent slug to confirm")

    expect(deleteButton).toBeDisabled()

    await user.type(input, "Triage agent")
    expect(deleteButton).toBeDisabled()

    await user.clear(input)
    await user.type(input, "triage-agent")
    expect(deleteButton).toBeEnabled()
  })
})
