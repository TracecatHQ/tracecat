import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type CaseTriggerRead,
  type TriggerRead,
  triggersCreateTrigger,
  triggersGetCaseTrigger,
  triggersGetTrigger,
  triggersListTriggers,
} from "@/client"
import { describeCaseTrigger } from "@/components/triggers/case-trigger/case-trigger-trigger-details"
import { CreateTriggerDialog } from "@/components/triggers/create-trigger-dialog"
import { TriggerDetail } from "@/components/triggers/trigger-detail"
import { WorkspaceTriggers } from "@/components/triggers/workspace-triggers"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  // The webhook form's method and status choices.
  $WebhookMethod: { enum: ["GET", "POST"] },
  $WebhookStatus: { enum: ["online", "offline"] },
  triggersCreateTrigger: jest.fn(),
  triggersGetCaseTrigger: jest.fn(),
  triggersGetTrigger: jest.fn(),
  triggersListTriggers: jest.fn(),
}))

let scopes: Record<string, boolean | undefined> = {}
let entitled = true
const push = jest.fn()

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes[scope],
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => key === "case_addons" && entitled,
    hasEntitlementData: true,
    isLoading: false,
  }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))
let workflows: { id: string; title: string; version: number | null }[] = []
jest.mock("@/lib/hooks", () => ({
  useWorkflowManager: () => ({ workflows, workflowsLoading: false }),
  useCaseTagCatalog: () => ({ caseTags: [], caseTagsIsLoading: false }),
}))
// The tag pickers' values, as text: the form's prefill is what is checked.
jest.mock("@/components/tags-input", () => ({
  MultiTagCommandInput: ({
    value,
    placeholder,
  }: {
    value: string[]
    placeholder: string
  }) => <div aria-label={placeholder}>{value.join(", ")}</div>,
}))
jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

const WORKFLOW_ID = "wf_3nF1fBrbY6AIX7Mr2qhEpR"

const CASE_TRIGGER: TriggerRead = {
  id: "trigger-4",
  kind: "case_trigger",
  name: "Closed cases case trigger",
  status: "online",
  config: {
    kind: "case_trigger",
    workflow_id: WORKFLOW_ID,
    case_trigger_id: "case-trigger-1",
    event_types: ["case_closed", "case_created"],
    tag_filters: ["phishing"],
  },
  bindings: [
    {
      id: "binding-5",
      trigger_id: "trigger-4",
      target_type: "workflow",
      target_id: "6f1c2a7e-3b4d-4e5f-9a1b-2c3d4e5f6a7b",
      target_name: "Closed cases",
      instruction: null,
      status: "online",
      created_at: "2026-11-27T00:00:00Z",
      updated_at: "2026-11-27T00:00:00Z",
    },
  ],
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
}

function saved(overrides: Partial<CaseTriggerRead> = {}): CaseTriggerRead {
  return {
    id: "case-trigger-1",
    workflow_id: "wf_closed",
    status: "offline",
    event_types: ["case_closed"],
    tag_filters: ["phishing"],
    ...overrides,
  }
}

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
}

async function pickCaseTriggerWorkflow(
  user: ReturnType<typeof userEvent.setup>
) {
  await user.click(screen.getByRole("button", { name: /Case event/ }))
  await user.type(screen.getByLabelText("Name"), "Closed cases case trigger")
  await user.click(screen.getByRole("combobox", { name: "Workflow" }))
  await user.click(await screen.findByRole("option", { name: "Closed cases" }))
}

beforeEach(() => {
  jest.clearAllMocks()
  entitled = true
  workflows = [{ id: "wf_closed", title: "Closed cases", version: 1 }]
  scopes = {
    "trigger:read": true,
    "trigger:create": true,
    "trigger:update": true,
    "trigger:delete": true,
    "workflow:read": true,
    "workflow:update": true,
    "workflow:execute": true,
  }
})

describe("Case trigger triggers", () => {
  // jsdom lacks the pointer capture API that Radix Select calls on open.
  beforeAll(() => {
    Element.prototype.hasPointerCapture = () => false
    Element.prototype.releasePointerCapture = () => {}
    Element.prototype.scrollIntoView = () => {}
  })

  it("lists a case trigger with its events and tags", async () => {
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [CASE_TRIGGER], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    const row = await screen.findByRole("link", {
      name: /Closed cases case trigger/,
    })
    expect(row).toHaveTextContent("Case event")
    expect(row).toHaveTextContent("Case closed, Case created · 1 tag")
    expect(describeCaseTrigger(CASE_TRIGGER)).toBe(
      "Case closed, Case created · 1 tag"
    )
  })

  it("says the plan hides them, naming one kind", async () => {
    entitled = false
    jest.mocked(triggersListTriggers).mockResolvedValue({
      items: [],
      has_more: false,
      locked: [{ kind: "case_trigger", entitlement: "case_addons", count: 2 }],
    })

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByText(
        "2 case event triggers are hidden because your plan doesn't include that kind. Nothing was deleted."
      )
    ).toBeInTheDocument()
  })

  it("shows its settings and keeps its one target fixed", async () => {
    jest.mocked(triggersGetTrigger).mockResolvedValue(CASE_TRIGGER)

    renderWithClient(<TriggerDetail triggerId="trigger-4" />)

    expect(
      await screen.findByText("Case closed, Case created")
    ).toBeInTheDocument()
    expect(screen.getByText("phishing")).toBeInTheDocument()
    expect(
      screen.getByText(/A case trigger runs the workflow it belongs to/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add target" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("switch", { name: "Closed cases case trigger online" })
    ).toBeEnabled()
  })

  it("marks Case event locked when the plan lacks case add-ons", () => {
    entitled = false

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    const caseTrigger = screen.getByRole("button", { name: /Case event/ })
    expect(caseTrigger).toBeDisabled()
    expect(caseTrigger).toHaveTextContent("Upgrade required")
    expect(screen.getByRole("button", { name: /Webhook/ })).toBeEnabled()
  })

  it("marks Case event when the caller can't change workflows", () => {
    scopes = { ...scopes, "workflow:update": false }

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    const caseTrigger = screen.getByRole("button", { name: /Case event/ })
    expect(caseTrigger).toBeDisabled()
    expect(caseTrigger).toHaveTextContent("No permission")
  })

  it("starts from the workflow's saved case trigger and sends no status", async () => {
    const user = userEvent.setup()
    jest.mocked(triggersGetCaseTrigger).mockResolvedValue(saved())
    jest.mocked(triggersCreateTrigger).mockResolvedValue(CASE_TRIGGER)

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await pickCaseTriggerWorkflow(user)
    await waitFor(() =>
      expect(screen.getByLabelText("Select case events...")).toHaveTextContent(
        "case_closed"
      )
    )
    expect(screen.getByLabelText("Select case tags...")).toHaveTextContent(
      "phishing"
    )
    await user.click(screen.getByRole("button", { name: "Create trigger" }))

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        "/workspaces/workspace-1/triggers/trigger-4"
      )
    )
    expect(triggersGetCaseTrigger).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      workflowId: "wf_closed",
    })
    expect(triggersCreateTrigger).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        name: "Closed cases case trigger",
        config: {
          kind: "case_trigger",
          workflow_id: "wf_closed",
          event_types: ["case_closed"],
          tag_filters: ["phishing"],
        },
      },
    })
  })

  it("says a live case trigger stays online", async () => {
    const user = userEvent.setup()
    jest
      .mocked(triggersGetCaseTrigger)
      .mockResolvedValue(saved({ status: "online" }))
    jest.mocked(triggersCreateTrigger).mockResolvedValue(CASE_TRIGGER)

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await pickCaseTriggerWorkflow(user)
    expect(
      await screen.findByText("This case trigger is online and stays online.")
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Create trigger" }))

    await waitFor(() => expect(triggersCreateTrigger).toHaveBeenCalledTimes(1))
    const [call] = jest.mocked(triggersCreateTrigger).mock.calls
    expect(call[0].requestBody).not.toHaveProperty("status")
  })

  it("won't create until the workflow's saved settings load, and retries", async () => {
    const user = userEvent.setup()
    jest
      .mocked(triggersGetCaseTrigger)
      .mockRejectedValueOnce(
        Object.assign(new Error("Unavailable"), { status: 503 })
      )
      .mockResolvedValue(saved())

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await pickCaseTriggerWorkflow(user)
    expect(
      await screen.findByText(
        /Couldn't load this workflow's case trigger settings/
      )
    ).toBeInTheDocument()

    // The failed pick is cleared, so Create has nothing to submit.
    await user.click(screen.getByRole("button", { name: "Create trigger" }))
    expect(await screen.findByText("Pick a workflow.")).toBeInTheDocument()
    expect(triggersCreateTrigger).not.toHaveBeenCalled()

    // Picking the same workflow again reads its settings again.
    await user.click(screen.getByRole("combobox", { name: "Workflow" }))
    await user.click(
      await screen.findByRole("option", { name: "Closed cases" })
    )
    await waitFor(() => expect(triggersGetCaseTrigger).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getByLabelText("Select case events...")).toHaveTextContent(
        "case_closed"
      )
    )
  })
})

describe("CreateTriggerDialog for a case trigger already a trigger", () => {
  it("marks workflows whose case trigger is already a trigger", async () => {
    const user = userEvent.setup()
    workflows = [
      { id: WORKFLOW_ID, title: "Listed", version: 1 },
      { id: "wf_closed", title: "Closed cases", version: 1 },
    ]
    jest.mocked(triggersListTriggers).mockResolvedValue({
      items: [CASE_TRIGGER],
      has_more: false,
      locked: [],
    })

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await user.click(screen.getByRole("button", { name: /Case event/ }))
    await user.click(screen.getByRole("combobox", { name: "Workflow" }))

    expect(
      await screen.findByRole("option", { name: /Listed.*Already a trigger/ })
    ).toHaveAttribute("aria-disabled", "true")
    expect(
      screen.getByRole("option", { name: "Closed cases" })
    ).not.toHaveAttribute("aria-disabled")
    expect(triggersListTriggers).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "case_trigger" })
    )
  })
})
