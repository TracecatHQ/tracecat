import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { VariableReadMinimal } from "@/client"
import {
  EditVariableDialog,
  EditVariableDialogTrigger,
} from "@/components/workspaces/edit-workspace-variable"

const mockUpdateVariableById = jest.fn()

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws",
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceVariables: () => ({ updateVariableById: mockUpdateVariableById }),
}))

const variable = {
  id: "var-1",
  name: "api_config",
  description: null,
  environment: "default",
  values: { base_url: "https://example.com" },
} as unknown as VariableReadMinimal

async function openDialog() {
  const user = userEvent.setup()
  render(
    <EditVariableDialog
      selectedVariable={variable}
      setSelectedVariable={jest.fn()}
    >
      <EditVariableDialogTrigger>Open</EditVariableDialogTrigger>
    </EditVariableDialog>
  )
  await user.click(screen.getByRole("button", { name: "Open" }))
  return user
}

beforeEach(() => {
  mockUpdateVariableById.mockReset()
})

test("shows an inline error and keeps the dialog open for an invalid name", async () => {
  const user = await openDialog()

  await user.type(screen.getByPlaceholderText("api_config"), "Api Config")
  await user.click(screen.getByRole("button", { name: /Save/ }))

  expect(
    await screen.findByText(
      "Use lowercase letters, numbers, and underscores only (e.g. api_config)."
    )
  ).toBeVisible()
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(mockUpdateVariableById).not.toHaveBeenCalled()
})

test("allows saving without changing the name", async () => {
  const user = await openDialog()

  await user.click(screen.getByRole("button", { name: /Save/ }))

  await waitFor(() => expect(mockUpdateVariableById).toHaveBeenCalledTimes(1))
  expect(mockUpdateVariableById.mock.calls[0][0].params.name).toBeUndefined()
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  )
})

test("shows an inline error for an empty value key", async () => {
  const user = await openDialog()

  await user.clear(screen.getByPlaceholderText("Key"))
  await user.click(screen.getByRole("button", { name: /Save/ }))

  expect(await screen.findByText("Key is required.")).toBeVisible()
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(mockUpdateVariableById).not.toHaveBeenCalled()
})
