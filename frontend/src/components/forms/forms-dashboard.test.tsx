import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { type FormReadMinimal, formsDeleteForm, formsListForms } from "@/client"
import { FormsDashboard } from "@/components/forms/forms-dashboard"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsDeleteForm: jest.fn(),
  formsListForms: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/lib/hooks", () => ({}))

function form(overrides: Partial<FormReadMinimal>): FormReadMinimal {
  return {
    id: "form-1",
    workflow_id: "wf_taken",
    workflow_title: "Provision laptop",
    status: "online",
    title: "Laptop request",
    submission_count: 42,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    ...overrides,
  }
}

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>
  )
  return userEvent.setup()
}

beforeEach(() => {
  jest.clearAllMocks()
  // Two pages: the list loads all of them so it can be searched.
  jest
    .mocked(formsListForms)
    .mockResolvedValueOnce({
      items: [form({})],
      next_cursor: "cursor-2",
      has_more: true,
    } as never)
    .mockResolvedValueOnce({
      items: [
        form({
          id: "form-2",
          title: "Offboarding",
          workflow_id: "wf_other",
          workflow_title: "Offboard employee",
          status: "offline",
          submission_count: 1,
        }),
      ],
      has_more: false,
    } as never)
})

it("lists every form with its workflow, status and usage, and deletes after confirming", async () => {
  jest.mocked(formsDeleteForm).mockResolvedValue(undefined)
  const user = renderWithClient(<FormsDashboard />)

  const row = (
    await screen.findByRole("link", { name: "Offboarding" })
  ).closest("li") as HTMLElement
  expect(
    within(row).getByRole("link", { name: "Offboard employee" })
  ).toHaveAttribute("href", "/workspaces/workspace-1/workflows/wf_other")
  expect(within(row).getByText("Offline")).toBeInTheDocument()
  expect(within(row).getByText("1 submission")).toBeInTheDocument()

  await user.type(screen.getByPlaceholderText("Search forms..."), "laptop")
  expect(screen.queryByText("Offboarding")).not.toBeInTheDocument()

  await user.click(
    screen.getByRole("button", { name: "Open menu for Laptop request" })
  )
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }))
  await user.click(screen.getByRole("button", { name: "Delete" }))
  await waitFor(() =>
    expect(formsDeleteForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
    })
  )
})
