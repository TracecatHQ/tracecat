/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type { CaseFieldReadMinimal } from "@/client"
import { AddCustomFieldDialog } from "@/components/cases/add-custom-field-dialog"
import { EditCustomFieldDialog } from "@/components/cases/edit-custom-field-dialog"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockCasesCreateField = jest.fn()
const mockCasesUpdateField = jest.fn()

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  casesCreateField: (...args: unknown[]) => mockCasesCreateField(...args),
  casesUpdateField: (...args: unknown[]) => mockCasesUpdateField(...args),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/lib/hooks", () => ({
  useCaseFields: () => ({ caseFields: [{ id: "field_2fa_status" }] }),
}))

beforeEach(() => {
  jest.clearAllMocks()
  mockCasesCreateField.mockResolvedValue(undefined)
  mockCasesUpdateField.mockResolvedValue(undefined)
})

function renderWithQueryClient(children: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

const existingField: CaseFieldReadMinimal = {
  id: "analyst_verdict_v2",
  display_name: "Analyst verdict",
  type: "TEXT",
  description: "",
  nullable: true,
  default: null,
  reserved: false,
  options: null,
  kind: null,
  required_on_closure: false,
}

describe("custom field metadata dialogs", () => {
  it("creates a field with a unique derived reference", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <AddCustomFieldDialog open onOpenChange={jest.fn()} />
    )

    await user.type(screen.getByRole("textbox", { name: "Name" }), "2FA status")
    expect(
      screen.getByText("Reference: field_2fa_status_2")
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add field" }))

    await waitFor(() => {
      expect(mockCasesCreateField).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          name: "field_2fa_status_2",
          display_name: "2FA status",
        }),
      })
    })
  })

  it("updates the display name without renaming the reference", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <EditCustomFieldDialog
        open
        field={existingField}
        onOpenChange={jest.fn()}
      />
    )

    const nameInput = screen.getByRole("textbox", { name: "Name" })
    const referenceInput = screen.getByRole("textbox", { name: "Reference" })
    await user.clear(nameInput)
    await user.type(nameInput, "2FA determination")
    expect(referenceInput).toHaveValue("analyst_verdict_v2")
    expect(referenceInput).toHaveAccessibleDescription(
      "Used in APIs and workflows. Changing it may break existing references."
    )
    await user.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() => {
      expect(mockCasesUpdateField).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        fieldId: "analyst_verdict_v2",
        requestBody: expect.objectContaining({
          display_name: "2FA determination",
        }),
      })
    })
    expect(
      mockCasesUpdateField.mock.calls[0][0].requestBody
    ).not.toHaveProperty("name")
  })

  it("validates and updates the reference independently", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <EditCustomFieldDialog
        open
        field={existingField}
        onOpenChange={jest.fn()}
      />
    )

    const referenceInput = screen.getByRole("textbox", { name: "Reference" })
    await user.clear(referenceInput)
    await user.type(referenceInput, "2FA determination")
    await user.click(screen.getByRole("button", { name: "Save changes" }))

    expect(
      await screen.findByText(
        "Reference must start with a letter or underscore and contain only letters, numbers, and underscores"
      )
    ).toBeInTheDocument()
    expect(mockCasesUpdateField).not.toHaveBeenCalled()

    await user.clear(referenceInput)
    await user.type(referenceInput, "final_determination")
    await user.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() => {
      expect(mockCasesUpdateField).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        fieldId: "analyst_verdict_v2",
        requestBody: expect.objectContaining({
          name: "final_determination",
          display_name: "Analyst verdict",
        }),
      })
    })
  })
})
