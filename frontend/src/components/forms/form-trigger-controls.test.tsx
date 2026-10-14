import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type FormRead,
  formsCreateForm,
  formsGetForm,
  formsListForms,
  formsUpdateForm,
} from "@/client"
import { FormTriggerControls } from "@/components/forms/form-trigger-controls"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsCreateForm: jest.fn(),
  formsGetForm: jest.fn(),
  formsListForms: jest.fn(),
  formsUpdateForm: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const FORM = {
  id: "form-1",
  workflow_id: "wf_3KbAYlkEEToril859NNEOb",
  status: "offline",
  url: "https://app.example.com/forms/form-1",
} as FormRead

function renderControls(version: number | null, forms: FormRead[]) {
  jest.mocked(formsListForms).mockResolvedValue({
    items: forms,
    has_more: false,
  } as never)
  jest.mocked(formsGetForm).mockResolvedValue(FORM)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<FormTriggerControls workflow={{ id: FORM.workflow_id, version }} />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
  return screen.findByRole("switch", { name: "Enable form" })
}

beforeEach(() => jest.clearAllMocks())

it("stays off until the workflow is published", async () => {
  expect(await renderControls(null, [])).toBeDisabled()
  expect(
    screen.getByText("Publish the workflow to put its form online")
  ).toBeInTheDocument()
})

it("creates an online form for a workflow without one", async () => {
  jest.mocked(formsCreateForm).mockResolvedValue(FORM)

  await userEvent.click(await renderControls(3, []))

  await waitFor(() =>
    expect(formsCreateForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: { workflow_id: FORM.workflow_id, status: "online" },
    })
  )
})

it("finds the workflow's form by its short ID and puts it online", async () => {
  jest.mocked(formsUpdateForm).mockResolvedValue(FORM)

  const toggle = await renderControls(3, [FORM])

  expect(await screen.findByLabelText("Requester link")).toHaveValue(FORM.url)
  expect(screen.getByRole("link", { name: "Edit in Forms" })).toHaveAttribute(
    "href",
    "/workspaces/workspace-1/forms/form-1"
  )
  await userEvent.click(toggle)
  await waitFor(() =>
    expect(formsUpdateForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
      requestBody: { status: "online" },
    })
  )
  expect(formsCreateForm).not.toHaveBeenCalled()
})
