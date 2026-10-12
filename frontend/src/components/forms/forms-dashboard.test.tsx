import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { type FormReadMinimal, formsDeleteForm, formsListForms } from "@/client"
import { FormsDashboard } from "@/components/forms/forms-dashboard"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

let scopes = new Set<string>()

jest.mock("@/client", () => ({
  formsListForms: jest.fn(),
  formsDeleteForm: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes.has(scope),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const listForms = jest.mocked(formsListForms)
const deleteForm = jest.mocked(formsDeleteForm)

function form(overrides: Partial<FormReadMinimal>): FormReadMinimal {
  return {
    id: "form-1",
    workflow_id: "00000000-0000-4000-8000-000000000001",
    workflow_title: "Provision laptop",
    status: "online",
    title: "Laptop request",
    submission_count: 42,
    last_submitted_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    ...overrides,
  }
}

const FORMS = [
  form({}),
  form({
    id: "form-2",
    title: "Report a phishing email",
    workflow_title: "Triage phishing report",
    submission_count: 1,
  }),
  form({
    id: "form-3",
    title: "Offboarding checklist",
    workflow_title: "Offboard employee",
    status: "offline",
    submission_count: 0,
    last_submitted_at: null,
  }),
]

function page(items: FormReadMinimal[], nextCursor: string | null = null) {
  return {
    items,
    next_cursor: nextCursor,
    prev_cursor: null,
    has_more: nextCursor !== null,
    has_previous: false,
  }
}

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<FormsDashboard />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
  return { user: userEvent.setup() }
}

const row = (title: string) =>
  screen.getByRole("link", { name: title }).closest("li") as HTMLElement

beforeEach(() => {
  jest.clearAllMocks()
  scopes = new Set(["form:read", "form:update", "form:delete"])
  listForms.mockResolvedValue(page(FORMS))
})

describe("FormsDashboard", () => {
  it("lists each form with its workflow, status and usage", async () => {
    renderDashboard()

    const laptop = within(await waitFor(() => row("Laptop request")))
    expect(
      laptop.getByRole("link", { name: "Laptop request" })
    ).toHaveAttribute("href", "/workspaces/workspace-1/forms/form-1")
    expect(
      laptop.getByRole("link", { name: "Provision laptop" })
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/00000000-0000-4000-8000-000000000001"
    )
    expect(laptop.getByText("Online")).toBeInTheDocument()
    expect(laptop.getByText("42 submissions")).toBeInTheDocument()
    expect(laptop.getByText("2h ago")).toBeInTheDocument()

    expect(
      within(row("Report a phishing email")).getByText("1 submission")
    ).toBeInTheDocument()
    const offboarding = within(row("Offboarding checklist"))
    expect(offboarding.getByText("Offline")).toBeInTheDocument()
    expect(offboarding.getByText("0 submissions")).toBeInTheDocument()
    expect(offboarding.queryByText(/ago$/)).not.toBeInTheDocument()
    expect(screen.getByText("3 forms")).toBeInTheDocument()
  })

  it("loads every page of forms", async () => {
    listForms
      .mockResolvedValueOnce(page(FORMS.slice(0, 2), "cursor-2"))
      .mockResolvedValueOnce(page(FORMS.slice(2)))

    renderDashboard()

    expect(
      await screen.findByRole("link", { name: "Offboarding checklist" })
    ).toBeInTheDocument()
    expect(listForms).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        workspaceId: "workspace-1",
        cursor: "cursor-2",
      })
    )
    expect(screen.getByText("3 forms")).toBeInTheDocument()
  })

  it("filters by form or workflow name", async () => {
    const { user } = renderDashboard()
    await screen.findByRole("link", { name: "Laptop request" })

    await user.type(screen.getByPlaceholderText("Search forms..."), "phish")

    expect(
      screen.getByRole("link", { name: "Report a phishing email" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Laptop request" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("1 form")).toBeInTheDocument()

    await user.clear(screen.getByPlaceholderText("Search forms..."))
    await user.type(
      screen.getByPlaceholderText("Search forms..."),
      "offboard emp"
    )
    expect(
      screen.getByRole("link", { name: "Offboarding checklist" })
    ).toBeInTheDocument()

    await user.clear(screen.getByPlaceholderText("Search forms..."))
    await user.type(screen.getByPlaceholderText("Search forms..."), "nothing")
    expect(screen.getByText("No forms found")).toBeInTheDocument()
  })

  it("leaves out the submission count the server withholds", async () => {
    listForms.mockResolvedValue(
      page([form({ submission_count: null, last_submitted_at: null })])
    )

    renderDashboard()

    const laptop = within(await waitFor(() => row("Laptop request")))
    expect(laptop.queryByText(/submission/)).not.toBeInTheDocument()
  })

  it("shows an empty state with no forms", async () => {
    listForms.mockResolvedValue(page([]))

    renderDashboard()

    expect(await screen.findByText("No forms yet")).toBeInTheDocument()
    expect(screen.getByText("0 forms")).toBeInTheDocument()
  })

  it("reports a failed load", async () => {
    listForms.mockRejectedValue(new Error("boom"))

    renderDashboard()

    expect(await screen.findByText("Error loading forms.")).toBeInTheDocument()
  })

  describe("row menu", () => {
    async function openMenu(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByRole("link", { name: "Laptop request" })
      await user.click(
        screen.getByRole("button", { name: "Open menu for Laptop request" })
      )
    }

    it("opens the requester page and deletes after confirmation", async () => {
      deleteForm.mockResolvedValue(undefined)
      const { user } = renderDashboard()
      await openMenu(user)

      expect(
        screen.getByRole("menuitem", { name: "Open form" })
      ).toHaveAttribute("href", "/forms/form-1")
      expect(
        screen.getByRole("menuitem", { name: "Copy link" })
      ).toBeInTheDocument()

      await user.click(screen.getByRole("menuitem", { name: "Delete" }))
      const dialog = await screen.findByRole("alertdialog")
      expect(dialog).toHaveTextContent("Delete Laptop request?")
      expect(dialog).toHaveTextContent("submissions and activity")
      expect(deleteForm).not.toHaveBeenCalled()

      await user.click(within(dialog).getByRole("button", { name: "Delete" }))

      await waitFor(() =>
        expect(deleteForm).toHaveBeenCalledWith({
          workspaceId: "workspace-1",
          formId: "form-1",
        })
      )
      await waitFor(() =>
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
      )
    })

    it("has no Delete without form:delete", async () => {
      scopes.delete("form:delete")
      const { user } = renderDashboard()
      await openMenu(user)

      expect(
        screen.getByRole("menuitem", { name: "Open form" })
      ).toBeInTheDocument()
      expect(
        screen.queryByRole("menuitem", { name: "Delete" })
      ).not.toBeInTheDocument()
    })
  })
})
