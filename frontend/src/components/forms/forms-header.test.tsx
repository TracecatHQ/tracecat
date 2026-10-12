import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { type FormRead, formsGetForm, formsUpdateForm } from "@/client"
import {
  FormDetailActions,
  FormDetailTitle,
  FormsListActions,
} from "@/components/forms/forms-header"
import { toast } from "@/components/ui/use-toast"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { QueryClient, QueryClientProvider } from "@/lib/query"

let scopes = new Set<string>()

jest.mock("@/client", () => ({
  formsGetForm: jest.fn(),
  formsUpdateForm: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes.has(scope),
}))

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/components/forms/create-form-dialog", () => ({
  CreateFormDialog: ({ open }: { open: boolean }) =>
    open ? <div role="dialog">New form dialog</div> : null,
}))

jest.mock("@/components/workspace-sync/resource-sync-actions", () => ({
  WorkspaceResourceSyncActions: ({
    label,
    resources,
  }: {
    label: string
    resources: string[]
  }) => (
    <button type="button">
      Push {label} ({resources.join(",")})
    </button>
  ),
}))

const getForm = jest.mocked(formsGetForm)
const updateForm = jest.mocked(formsUpdateForm)
const mockUseFormsAccess = jest.mocked(useFormsAccess)
const ids = { workspaceId: "workspace-1", formId: "form-1" }

function formRead(overrides: Partial<FormRead> = {}): FormRead {
  return {
    id: "form-1",
    workspace_id: "workspace-1",
    workflow_id: "00000000-0000-4000-8000-000000000001",
    workflow_title: "Provision laptop",
    workflow_version: 4,
    status: "offline",
    config: { title: "Laptop request" },
    issues: [],
    url: "https://app.example.com/forms/form-1",
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    ...overrides,
  }
}

function renderWithQuery(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
  return userEvent.setup()
}

beforeEach(() => {
  jest.clearAllMocks()
  scopes = new Set(["form:read", "form:create", "form:update"])
  mockUseFormsAccess.mockReturnValue("enabled")
  getForm.mockResolvedValue(formRead())
})

describe("FormsListActions", () => {
  it("offers the Git push for workflows and a new form", async () => {
    const user = renderWithQuery(<FormsListActions />)

    // Forms sync inside the workflow file; there is no form resource.
    expect(
      screen.getByRole("button", {
        name: "Push workflows and forms (workflow)",
      })
    ).toBeInTheDocument()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "New form" }))

    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("has no New form without form:create", () => {
    scopes.delete("form:create")

    renderWithQuery(<FormsListActions />)

    expect(screen.queryByRole("button", { name: "New form" })).toBeNull()
  })

  it.each(["locked", "loading", "hidden"] as const)(
    "renders nothing while access is %s",
    (access) => {
      mockUseFormsAccess.mockReturnValue(access)

      renderWithQuery(<FormsListActions />)

      expect(screen.queryByRole("button")).toBeNull()
    }
  )
})

describe("FormDetailTitle", () => {
  it("shows the breadcrumb and puts the form online", async () => {
    updateForm.mockResolvedValue(formRead({ status: "online" }))
    const user = renderWithQuery(<FormDetailTitle {...ids} />)

    expect(await screen.findByText("Laptop request")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Forms" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/forms"
    )
    const toggle = screen.getByRole("switch", { name: "Form online" })
    expect(toggle).not.toBeChecked()
    expect(screen.getByText("Offline")).toBeInTheDocument()

    await user.click(toggle)

    await waitFor(() =>
      expect(updateForm).toHaveBeenCalledWith({
        ...ids,
        requestBody: { status: "online" },
      })
    )
    await waitFor(() => expect(toggle).toBeChecked())
    expect(screen.getByText("Online")).toBeInTheDocument()
  })

  it("falls back to the workflow title for a form with no title", async () => {
    getForm.mockResolvedValue(formRead({ config: {} }))

    renderWithQuery(<FormDetailTitle {...ids} />)

    expect(await screen.findByText("Provision laptop")).toBeInTheDocument()
  })

  it("says why the form could not go online", async () => {
    updateForm.mockRejectedValue(
      Object.assign(new Error("Conflict"), {
        status: 409,
        body: {
          detail: {
            code: "form_workflow_unpublished",
            message: "Publish the workflow before putting its form online",
          },
        },
      })
    )
    const user = renderWithQuery(<FormDetailTitle {...ids} />)

    await user.click(await screen.findByRole("switch", { name: "Form online" }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't put the form online",
          description: "Publish the workflow before putting its form online",
        })
      )
    )
    expect(
      screen.getByRole("switch", { name: "Form online" })
    ).not.toBeChecked()
  })

  it("shows the status read-only without form:update", async () => {
    scopes.delete("form:update")

    renderWithQuery(<FormDetailTitle {...ids} />)

    expect(
      await screen.findByRole("switch", { name: "Form online" })
    ).toBeDisabled()
  })

  it("does not load the form while Forms is locked", () => {
    mockUseFormsAccess.mockReturnValue("locked")

    renderWithQuery(<FormDetailTitle {...ids} />)

    expect(screen.getByRole("heading", { name: "Forms" })).toBeInTheDocument()
    expect(getForm).not.toHaveBeenCalled()
  })

  it("names a form that cannot be opened", async () => {
    getForm.mockRejectedValue(
      Object.assign(new Error("Not found"), { status: 404 })
    )

    renderWithQuery(<FormDetailTitle {...ids} />)

    expect(await screen.findByText("Form not found")).toBeInTheDocument()
    expect(screen.queryByRole("switch")).toBeNull()
  })
})

describe("FormDetailActions", () => {
  it("opens the requester page in a new tab", async () => {
    renderWithQuery(<FormDetailActions {...ids} />)

    const open = await screen.findByRole("link", { name: "Open" })
    expect(open).toHaveAttribute("href", "/forms/form-1")
    expect(open).toHaveAttribute("target", "_blank")
  })

  it("renders nothing while Forms is locked", () => {
    mockUseFormsAccess.mockReturnValue("locked")

    renderWithQuery(<FormDetailActions {...ids} />)

    expect(screen.queryByRole("link")).toBeNull()
    expect(getForm).not.toHaveBeenCalled()
  })
})
