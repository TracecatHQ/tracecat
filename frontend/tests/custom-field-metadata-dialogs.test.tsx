/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type { CaseFieldReadMinimal } from "@/client"
import { AddCustomFieldDialog } from "@/components/cases/add-custom-field-dialog"
import { EditCustomFieldDialog } from "@/components/cases/edit-custom-field-dialog"

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

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
})

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
  it("creates a field with a display name and derived reference", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <AddCustomFieldDialog open onOpenChange={jest.fn()} />
    )

    await user.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Analyst Verdict"
    )
    expect(screen.getByText("Reference: analyst_verdict")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add field" }))

    await waitFor(() => {
      expect(mockCasesCreateField).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          name: "analyst_verdict",
          display_name: "Analyst Verdict",
        }),
      })
    })
  })

  it("creates a valid reference when the display name begins with a number", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <AddCustomFieldDialog open onOpenChange={jest.fn()} />
    )

    await user.type(screen.getByRole("textbox", { name: "Name" }), "2FA status")
    expect(screen.getByText("Reference: field_2fa_status")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add field" }))

    await waitFor(() => {
      expect(mockCasesCreateField).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        requestBody: expect.objectContaining({
          name: "field_2fa_status",
          display_name: "2FA status",
        }),
      })
    })
  })

  it("rejects display names that cannot produce a reference", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <AddCustomFieldDialog open onOpenChange={jest.fn()} />
    )

    await user.type(screen.getByRole("textbox", { name: "Name" }), "🚨")
    await user.click(screen.getByRole("button", { name: "Add field" }))

    expect(
      await screen.findByText(
        "Field name must contain at least one Latin letter or number"
      )
    ).toBeInTheDocument()
    expect(mockCasesCreateField).not.toHaveBeenCalled()
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
    expect(nameInput).toHaveValue("Analyst verdict")
    expect(referenceInput).toHaveValue("analyst_verdict_v2")
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

  it("updates the reference independently of the display name", async () => {
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
    await user.type(referenceInput, "final_determination")
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Analyst verdict"
    )
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

  it("validates an explicitly edited reference", async () => {
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
  })

  it("omits the reference when the display name is unchanged", async () => {
    const user = userEvent.setup()
    renderWithQueryClient(
      <EditCustomFieldDialog
        open
        field={existingField}
        onOpenChange={jest.fn()}
      />
    )

    await user.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() => {
      expect(mockCasesUpdateField).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        fieldId: "analyst_verdict_v2",
        requestBody: expect.objectContaining({
          display_name: "Analyst verdict",
        }),
      })
    })
    expect(
      mockCasesUpdateField.mock.calls[0][0].requestBody
    ).not.toHaveProperty("name")
  })
})
