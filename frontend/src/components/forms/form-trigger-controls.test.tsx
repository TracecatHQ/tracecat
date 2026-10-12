import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type FormRead,
  type FormReadMinimal,
  formsCreateForm,
  formsGetForm,
  formsListForms,
  formsUpdateForm,
} from "@/client"
import { FormTriggerControls } from "@/components/forms/form-trigger-controls"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

// Pair produced by the backend's `WorkflowUUID.short()`.
const WORKFLOW_SHORT_ID = "wf_3KbAYlkEEToril859NNEOb"
const WORKFLOW_UUID = "7b0c1f6e-3d2a-4e5b-9c8d-1a2b3c4d5e6f"

let scopes: Record<string, boolean | undefined> = {}

jest.mock("@/client", () => ({
  formsCreateForm: jest.fn(),
  formsGetForm: jest.fn(),
  formsListForms: jest.fn(),
  formsUpdateForm: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes[scope],
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const listForms = jest.mocked(formsListForms)
const getForm = jest.mocked(formsGetForm)
const createForm = jest.mocked(formsCreateForm)
const updateForm = jest.mocked(formsUpdateForm)

function listedForm(status: "online" | "offline"): FormReadMinimal {
  return {
    id: "form-1",
    workflow_id: WORKFLOW_UUID,
    workflow_title: "Provision laptop",
    status,
    title: "Laptop request",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

function formRead(status: "online" | "offline"): FormRead {
  return {
    id: "form-1",
    workspace_id: "workspace-1",
    workflow_id: WORKFLOW_UUID,
    workflow_title: "Provision laptop",
    workflow_version: 3,
    status,
    config: {},
    issues: [],
    url: "https://app.example.com/forms/form-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

function mockForms(status?: "online" | "offline") {
  listForms.mockResolvedValue({
    items: status ? [listedForm(status)] : [],
    has_more: false,
    has_previous: false,
  } as never)
  if (status) {
    getForm.mockResolvedValue(formRead(status) as never)
  }
}

function renderControls(version: number | null = 3) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    )
  }
  return render(
    <FormTriggerControls workflow={{ id: WORKFLOW_SHORT_ID, version }} />,
    { wrapper }
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  scopes = { "form:create": true, "form:update": true }
})

describe("FormTriggerControls", () => {
  it("disables the switch with a hint until the workflow is published", async () => {
    mockForms()

    renderControls(null)

    const toggle = await screen.findByRole("switch", { name: "Enable form" })
    expect(toggle).toBeDisabled()
    expect(
      screen.getByText("Publish the workflow to put its form online")
    ).toBeInTheDocument()
    expect(screen.queryByText("Edit in Forms")).not.toBeInTheDocument()
  })

  it("creates an online form when turned on for a workflow without one", async () => {
    mockForms()
    createForm.mockResolvedValue(formRead("online") as never)

    renderControls()

    const toggle = await screen.findByRole("switch", { name: "Enable form" })
    expect(toggle).toBeEnabled()
    expect(toggle).not.toBeChecked()
    expect(
      screen.getByText("Turn on to create a form from this workflow's inputs")
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Requester link")).not.toBeInTheDocument()

    await userEvent.click(toggle)

    await waitFor(() =>
      expect(createForm).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        requestBody: { workflow_id: WORKFLOW_SHORT_ID, status: "online" },
      })
    )
    expect(updateForm).not.toHaveBeenCalled()
  })

  it("shows an online form's link and takes it offline", async () => {
    mockForms("online")
    updateForm.mockResolvedValue(formRead("offline") as never)

    renderControls()

    const toggle = await screen.findByRole("switch", { name: "Enable form" })
    expect(toggle).toBeChecked()
    expect(
      screen.getByText("Form is online and accepting submissions")
    ).toBeInTheDocument()
    expect(await screen.findByLabelText("Requester link")).toHaveValue(
      "https://app.example.com/forms/form-1"
    )
    expect(screen.getByRole("link", { name: "Edit in Forms" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/forms/form-1"
    )

    await userEvent.click(toggle)

    await waitFor(() =>
      expect(updateForm).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        formId: "form-1",
        requestBody: { status: "offline" },
      })
    )
    expect(createForm).not.toHaveBeenCalled()
  })

  it("puts an offline form online without creating another", async () => {
    mockForms("offline")
    updateForm.mockResolvedValue(formRead("online") as never)

    renderControls()

    const toggle = await screen.findByRole("switch", { name: "Enable form" })
    expect(toggle).not.toBeChecked()
    expect(screen.getByText("Form is offline")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Edit in Forms" })).toBeVisible()

    await userEvent.click(toggle)

    await waitFor(() =>
      expect(updateForm).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        formId: "form-1",
        requestBody: { status: "online" },
      })
    )
    expect(createForm).not.toHaveBeenCalled()
  })

  it("disables the switch for a caller who cannot create a form", async () => {
    mockForms()
    scopes = { "form:create": false, "form:update": false }

    renderControls()

    expect(
      await screen.findByRole("switch", { name: "Enable form" })
    ).toBeDisabled()
  })

  it("leaves the switch usable while scopes are still loading", async () => {
    mockForms("offline")
    scopes = {}

    renderControls()

    expect(
      await screen.findByRole("switch", { name: "Enable form" })
    ).toBeEnabled()
  })
})
