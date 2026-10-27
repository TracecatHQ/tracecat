import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  NewVariableDialog,
  NewVariableDialogTrigger,
} from "@/components/workspaces/add-workspace-variable"

const mockCreateVariable = jest.fn()

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws",
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceVariables: () => ({ createVariable: mockCreateVariable }),
}))

async function openDialog({ key = "base_url" }: { key?: string } = {}) {
  const user = userEvent.setup()
  render(
    <TooltipProvider>
      <NewVariableDialog>
        <NewVariableDialogTrigger>Open</NewVariableDialogTrigger>
      </NewVariableDialog>
    </TooltipProvider>
  )
  await user.click(screen.getByRole("button", { name: "Open" }))
  if (key) {
    await user.type(screen.getByPlaceholderText("Key"), key)
  }
  await user.type(screen.getByPlaceholderText("Value"), "https://example.com")
  return user
}

beforeEach(() => {
  mockCreateVariable.mockReset()
})

test("shows an inline error and keeps the dialog open for an invalid name", async () => {
  const user = await openDialog()

  await user.type(
    screen.getByPlaceholderText("Name (lowercase snake case)"),
    "my variable"
  )
  await user.click(screen.getByRole("button", { name: /Create variable/ }))

  expect(
    await screen.findByText(
      "Use lowercase letters, numbers, and underscores only (e.g. api_config)."
    )
  ).toBeVisible()
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(mockCreateVariable).not.toHaveBeenCalled()
})

test("submits a valid snake case name", async () => {
  const user = await openDialog()

  await user.type(
    screen.getByPlaceholderText("Name (lowercase snake case)"),
    "api_config"
  )
  await user.click(screen.getByRole("button", { name: /Create variable/ }))

  await waitFor(() => expect(mockCreateVariable).toHaveBeenCalledTimes(1))
  expect(mockCreateVariable.mock.calls[0][0]).toMatchObject({
    name: "api_config",
    environment: "default",
    values: { base_url: "https://example.com" },
  })
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  )
})

test("shows an inline error for an empty value key", async () => {
  const user = await openDialog({ key: "" })

  await user.type(
    screen.getByPlaceholderText("Name (lowercase snake case)"),
    "api_config"
  )
  await user.click(screen.getByRole("button", { name: /Create variable/ }))

  expect(await screen.findByText("Key is required.")).toBeVisible()
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(mockCreateVariable).not.toHaveBeenCalled()
})
