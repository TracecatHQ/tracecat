import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  formsCreateForm,
  formsListForms,
  type WorkflowReadMinimal,
} from "@/client"
import {
  CreateFormDialog,
  getFormWorkflowHint,
} from "@/components/forms/create-form-dialog"
import { toast } from "@/components/ui/use-toast"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const push = jest.fn()
let workflows: WorkflowReadMinimal[] = []

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))

jest.mock("@/client", () => ({
  formsCreateForm: jest.fn(),
  formsListForms: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useWorkflowManager: () => ({ workflows, workflowsLoading: false }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const createForm = jest.mocked(formsCreateForm)
const listForms = jest.mocked(formsListForms)

// Short ID and UUID of the same workflow, as the backend encodes them.
const TAKEN_SHORT_ID = "wf_3KbAYlkEEToril859NNEOb"
const TAKEN_UUID = "7b0c1f6e-3d2a-4e5b-9c8d-1a2b3c4d5e6f"
const FREE_SHORT_ID = "wf_000000001vGeH72LxVtxKh"

function workflow(
  overrides: Partial<WorkflowReadMinimal>
): WorkflowReadMinimal {
  return {
    id: FREE_SHORT_ID,
    title: "Grant SaaS access",
    description: "",
    status: "online",
    icon_url: null,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    version: 3,
    ...overrides,
  }
}

function renderDialog() {
  const onOpenChange = jest.fn()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<CreateFormDialog open onOpenChange={onOpenChange} />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  })
  return { onOpenChange, user: userEvent.setup() }
}

beforeEach(() => {
  jest.clearAllMocks()
  workflows = [
    workflow({}),
    workflow({ id: TAKEN_SHORT_ID, title: "Provision laptop" }),
    workflow({ id: "wf_draft", title: "Offboard employee", version: null }),
  ]
  listForms.mockResolvedValue({
    items: [
      {
        id: "form-1",
        workflow_id: TAKEN_UUID,
        workflow_title: "Provision laptop",
        status: "online",
        title: "Laptop request",
        created_at: "2026-10-01T09:00:00Z",
        updated_at: "2026-10-01T09:00:00Z",
      },
    ],
    next_cursor: null,
    prev_cursor: null,
    has_more: false,
    has_previous: false,
  })
})

describe("getFormWorkflowHint", () => {
  const forms = [{ workflow_id: TAKEN_UUID }]

  it("allows a published workflow with no form", () => {
    expect(getFormWorkflowHint(workflow({}), forms)).toBeNull()
  })

  it("matches a form to its workflow across the two ID formats", () => {
    expect(getFormWorkflowHint(workflow({ id: TAKEN_SHORT_ID }), forms)).toBe(
      "Already has a form"
    )
    expect(getFormWorkflowHint(workflow({ id: TAKEN_UUID }), forms)).toBe(
      "Already has a form"
    )
  })

  it("refuses a workflow that was never published", () => {
    expect(getFormWorkflowHint(workflow({ version: null }), forms)).toBe(
      "Publish the workflow first"
    )
  })
})

describe("CreateFormDialog", () => {
  it("disables workflows that have a form or are unpublished, with a hint", async () => {
    renderDialog()

    const taken = await screen.findByRole("option", {
      name: /Provision laptop/,
    })
    await waitFor(() => expect(taken).toHaveAttribute("aria-disabled", "true"))
    expect(taken).toHaveTextContent("Already has a form")

    const draft = screen.getByRole("option", { name: /Offboard employee/ })
    expect(draft).toHaveAttribute("aria-disabled", "true")
    expect(draft).toHaveTextContent("Publish the workflow first")

    const free = screen.getByRole("option", { name: /Grant SaaS access/ })
    expect(free).not.toHaveAttribute("aria-disabled", "true")
    expect(free).toHaveTextContent(/^Grant SaaS access$/)
  })

  it("does nothing when a disabled workflow is clicked", async () => {
    const { user } = renderDialog()
    const taken = await screen.findByRole("option", {
      name: /Provision laptop/,
    })
    await waitFor(() => expect(taken).toHaveAttribute("aria-disabled", "true"))

    await user.click(taken)

    expect(createForm).not.toHaveBeenCalled()
  })

  it("creates the form and opens its detail page", async () => {
    createForm.mockResolvedValue({ id: "form-9" } as Awaited<
      ReturnType<typeof formsCreateForm>
    >)
    const { user, onOpenChange } = renderDialog()

    await user.click(
      await screen.findByRole("option", { name: /Grant SaaS access/ })
    )

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/workspaces/workspace-1/forms/form-9")
    )
    expect(createForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: { workflow_id: FREE_SHORT_ID },
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("says why a form could not be created and stays open", async () => {
    createForm.mockRejectedValue(
      Object.assign(new Error("Conflict"), {
        status: 409,
        body: { detail: "This workflow already has a form" },
      })
    )
    const { user, onOpenChange } = renderDialog()

    await user.click(
      await screen.findByRole("option", { name: /Grant SaaS access/ })
    )

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't create the form",
          description: "This workflow already has a form",
        })
      )
    )
    expect(push).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it("filters workflows by name", async () => {
    const { user } = renderDialog()
    await screen.findByRole("option", { name: /Grant SaaS access/ })

    await user.type(screen.getByPlaceholderText("Search workflows..."), "saas")

    expect(
      screen.getByRole("option", { name: /Grant SaaS access/ })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: /Provision laptop/ })
    ).not.toBeInTheDocument()
  })
})
