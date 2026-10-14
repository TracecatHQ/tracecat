import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  formsListIntakeForms,
  formsListIntakeSubmissions,
  formsLocateIntakeForm,
  formsRenderForm,
  formsSubmitForm,
} from "@/client"
import {
  RequesterForm,
  RequesterHome,
  RequesterShell,
} from "@/components/forms/requester-pages"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { FormsAccess } from "@/hooks/use-forms-access"
import { fieldBlock, resolvedForm } from "@/lib/forms/test-fixtures"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const replace = jest.fn()
let searchParams = new URLSearchParams()
let access: FormsAccess = "enabled"
let signedIn = true
let workspaceScopes: string[] = ["form:read", "form:submit"]

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => searchParams,
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND")
  },
}))
jest.mock("@/client", () => ({
  formsListIntakeForms: jest.fn(),
  formsListIntakeSubmissions: jest.fn(),
  formsLocateIntakeForm: jest.fn(),
  formsRenderForm: jest.fn(),
  formsSearchFormUsers: jest.fn(),
  formsSubmitForm: jest.fn(),
}))
jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: signedIn
      ? {
          email: "maya.okafor@example.com",
          getDisplayName: () => "Maya Okafor",
        }
      : null,
    userIsLoading: false,
  }),
  useAuthActions: () => ({ logout: jest.fn() }),
}))
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => access,
}))
jest.mock("@/lib/hooks", () => ({
  useUserScopes: jest.fn(() => ({
    userScopes: { scopes: workspaceScopes },
    isLoading: false,
  })),
}))

const renderForm = jest.mocked(formsRenderForm)
const submit = jest.mocked(formsSubmitForm)

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
  return userEvent.setup()
}

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

beforeEach(() => {
  jest.clearAllMocks()
  access = "enabled"
  signedIn = true
  workspaceScopes = ["form:read", "form:submit"]
  searchParams = new URLSearchParams()
  jest.mocked(formsLocateIntakeForm).mockResolvedValue({
    form_id: "form-1",
    workspace_id: "workspace-1",
    workspace_name: "Security",
  })
  renderForm.mockResolvedValue(
    resolvedForm({
      blocks: [fieldBlock({ name: "reason", label: "Reason", required: true })],
    })
  )
})

describe("RequesterShell", () => {
  const renderShell = () =>
    render(
      <RequesterShell>
        <p>Form content</p>
      </RequesterShell>
    )

  it("sends a signed-out visitor to sign-in without reading access", () => {
    signedIn = false
    window.history.pushState({}, "", "/forms/form-1")

    renderShell()

    expect(replace).toHaveBeenCalledWith("/sign-in?returnUrl=%2Fforms%2Fform-1")
    expect(screen.queryByText("Form content")).not.toBeInTheDocument()
  })

  it("is a 404 with the flag off and a locked page without the entitlement", () => {
    access = "hidden"
    jest.spyOn(console, "error").mockImplementation(() => {})
    expect(renderShell).toThrow("NEXT_NOT_FOUND")

    access = "locked"
    renderShell()
    expect(screen.getByText("Forms aren't available")).toBeInTheDocument()
    expect(screen.queryByText("Form content")).not.toBeInTheDocument()
    jest.restoreAllMocks()
  })
})

describe("RequesterHome", () => {
  it("lists forms grouped by workspace, linking to their fill-in pages", async () => {
    const item = (id: string, workspace: string, title: string) => ({
      id,
      workspace_id: workspace,
      workspace_name: workspace,
      title,
      description: id === "form-1" ? "A **new** laptop." : null,
    })
    jest.mocked(formsListIntakeForms).mockResolvedValue({
      items: [
        item("form-1", "Security", "Laptop request"),
        item("form-2", "People", "New hire"),
        item("form-3", "Security", "Phishing"),
      ],
      has_more: false,
    })

    renderWithClient(<RequesterHome />)

    const security = (
      await screen.findByRole("heading", { name: "Security" })
    ).closest("section") as HTMLElement
    expect(
      within(security)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href"))
    ).toEqual(["/forms/form-1", "/forms/form-3"])
    expect(within(security).getByText("A new laptop.")).toBeInTheDocument()
    expect(formsListIntakeSubmissions).not.toHaveBeenCalled()
  })

  it("lists My submissions without any run status", async () => {
    searchParams = new URLSearchParams("tab=submissions")
    jest.mocked(formsListIntakeSubmissions).mockResolvedValue({
      items: [
        {
          id: "submission-1",
          reference: "FRM-8F3K2A1C",
          form_id: "form-1",
          form_title: "Laptop request",
          workspace_id: "Security",
          workspace_name: "Security",
          submitted_at: "2026-10-12T10:42:00Z",
        },
      ],
      has_more: false,
    } as never)

    renderWithClient(<RequesterHome />)

    expect(await screen.findByText("FRM-8F3K2A1C")).toBeInTheDocument()
    expect(
      screen.getAllByRole("columnheader").map((cell) => cell.textContent)
    ).toEqual(["Form", "Reference", "Submitted"])
    expect(formsListIntakeForms).not.toHaveBeenCalled()
  })
})

describe("RequesterForm", () => {
  async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
    await user.type(await screen.findByLabelText(/Reason/), "Screen cracked")
    await user.click(screen.getByRole("button", { name: "Submit" }))
  }

  it("locates, renders and submits with the rendered version, then confirms", async () => {
    submit.mockResolvedValue({
      id: "submission-1",
      reference: "FRM-8F3K2A1C",
      submitted_at: "2026-10-12T10:42:00Z",
    })
    const user = renderWithClient(<RequesterForm formId="form-1" />)

    await fillAndSubmit(user)

    expect(
      await screen.findByRole("heading", { name: "Request submitted" })
    ).toBeInTheDocument()
    expect(renderForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
    })
    expect(submit).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
      requestBody: {
        values: { reason: "Screen cracked" },
        definition_version: 4,
      },
    })
    expect(screen.getByText("Security")).toBeInTheDocument()
    expect(screen.getByText("FRM-8F3K2A1C")).toBeInTheDocument()
    // Rendering again would record another view.
    expect(renderForm).toHaveBeenCalledTimes(1)
  })

  it("reloads the form on form_changed and keeps the answers", async () => {
    submit.mockRejectedValueOnce(
      apiError(409, { code: "form_changed", message: "This form changed" })
    )
    const user = renderWithClient(<RequesterForm formId="form-1" />)
    await screen.findByLabelText(/Reason/)
    renderForm.mockResolvedValue(
      resolvedForm({
        definition_version: 5,
        blocks: [
          fieldBlock({ name: "reason", label: "Reason", required: true }),
          fieldBlock({ name: "notes", label: "Notes" }),
        ],
      })
    )

    await fillAndSubmit(user)

    expect(await screen.findByLabelText("Notes")).toBeInTheDocument()
    expect(screen.getByLabelText(/Reason/)).toHaveValue("Screen cracked")
    expect(
      screen.getByText(/This form was updated while you were filling it in/)
    ).toBeInTheDocument()
  })

  it.each([
    ["Form not found", apiError(404, "Form not found")],
    [
      "This form isn't accepting requests",
      apiError(409, { code: "form_offline", message: "Offline" }),
    ],
  ])(
    "shows %p instead of a form that cannot be filled in",
    async (title, error) => {
      renderForm.mockRejectedValue(error)

      renderWithClient(<RequesterForm formId="form-1" />)

      expect(await screen.findByText(title)).toBeInTheDocument()
      expect(
        screen.getByRole("link", { name: "Browse forms" })
      ).toHaveAttribute("href", "/forms")
    }
  )

  it("is read-only without form:submit", async () => {
    workspaceScopes = ["form:read"]

    renderWithClient(<RequesterForm formId="form-1" />)

    expect(
      await screen.findByText("You can view this form but you can't submit it.")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled()
  })
})
