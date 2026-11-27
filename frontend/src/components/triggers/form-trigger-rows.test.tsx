import { render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type FormReadMinimal,
  formsListForms,
  type TriggerRead,
  triggersListTriggers,
} from "@/client"
import { CreateTriggerDialog } from "@/components/triggers/create-trigger-dialog"
import { WorkspaceTriggers } from "@/components/triggers/workspace-triggers"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  $WebhookMethod: { enum: ["GET", "POST"] },
  $WebhookStatus: { enum: ["online", "offline"] },
  formsListForms: jest.fn(),
  triggersListTriggers: jest.fn(),
}))

let scopes: Record<string, boolean | undefined> = {}
let entitlements: Set<string> = new Set()
let entitlementsFailed = false

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes[scope],
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) =>
      !entitlementsFailed && entitlements.has(key),
    hasEntitlementData: !entitlementsFailed,
    isLoading: false,
  }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))
jest.mock("@/lib/hooks", () => ({
  useWorkflowManager: () => ({ workflows: [], workflowsLoading: false }),
}))
jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

const SCHEDULE: TriggerRead = {
  id: "trigger-1",
  kind: "schedule",
  name: "Nightly report",
  status: "online",
  config: {
    kind: "schedule",
    workflow_id: "wf_4itKqkgCZrLhgYiq5L211X",
    schedule_id: "sch-6f1c2a7e3b4d4e5f9a1b2c3d4e5f6a7b",
    cron: "0 2 * * *",
    timeout: 0,
  },
  bindings: [],
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
}

const FORM: FormReadMinimal = {
  id: "form-1",
  workflow_id: "wf_4itKqkgCZrLhgYiq5L211X",
  workflow_title: "Phishing triage",
  status: "online",
  title: "Report phishing",
  description: null,
  submission_count: 3,
  last_submitted_at: null,
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
}

// A 4xx: the triggers query's retry handler doesn't retry it.
const TRIGGERS_DENIED = Object.assign(new Error("Denied"), {
  status: 403,
  body: { detail: "Denied" },
})

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

beforeEach(() => {
  jest.clearAllMocks()
  entitlements = new Set(["forms"])
  entitlementsFailed = false
  scopes = {
    "trigger:read": true,
    "trigger:create": true,
    "form:read": true,
    "form:update": true,
    "workflow:read": true,
    "workflow:update": true,
    "workflow:execute": true,
    "schedule:create": true,
    "schedule:read": true,
  }
  jest
    .mocked(triggersListTriggers)
    .mockResolvedValue({ items: [SCHEDULE], has_more: false })
})

describe("Forms in the Triggers tab", () => {
  it("lists a form beside the triggers, linking to its Forms page", async () => {
    const offline = {
      ...FORM,
      id: "form-2",
      title: "Access request",
      workflow_title: "Grant access",
      status: "offline" as const,
    }
    jest
      .mocked(formsListForms)
      .mockResolvedValue({ items: [FORM, offline], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    const row = await screen.findByRole("link", { name: /Report phishing/ })
    expect(row).toHaveAttribute("href", "/workspaces/workspace-1/forms/form-1")
    expect(row).toHaveTextContent("Form")
    expect(row).toHaveTextContent("Phishing triage")
    expect(row).toHaveTextContent("Online")
    expect(
      screen.getByRole("link", { name: /Access request/ })
    ).toHaveTextContent("Offline")
    expect(
      screen.getByRole("link", { name: /Nightly report/ })
    ).toBeInTheDocument()
  })

  it("is read-only: a form row has no controls", async () => {
    jest
      .mocked(formsListForms)
      .mockResolvedValue({ items: [FORM], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    const row = await screen.findByRole("link", { name: /Report phishing/ })
    expect(within(row).queryByRole("button")).not.toBeInTheDocument()
    expect(within(row).queryByRole("switch")).not.toBeInTheDocument()
  })

  it("lists forms when the workspace has no triggers", async () => {
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [], has_more: false })
    jest
      .mocked(formsListForms)
      .mockResolvedValue({ items: [FORM], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByRole("link", { name: /Report phishing/ })
    ).toBeInTheDocument()
    expect(screen.queryByText("No triggers yet")).not.toBeInTheDocument()
  })

  it("loads every page of forms", async () => {
    const older = { ...FORM, id: "form-0", title: "Older form" }
    jest
      .mocked(formsListForms)
      .mockResolvedValueOnce({
        items: [FORM],
        has_more: true,
        next_cursor: "cursor-2",
      })
      .mockResolvedValueOnce({ items: [older], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByRole("link", { name: /Older form/ })
    ).toBeInTheDocument()
    expect(formsListForms).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "cursor-2" })
    )
  })

  it("asks for no forms without form:read", async () => {
    scopes = { ...scopes, "form:read": false }

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByRole("link", { name: /Nightly report/ })
    ).toBeInTheDocument()
    expect(formsListForms).not.toHaveBeenCalled()
  })

  it("asks for no forms when the plan lacks them, and says why", async () => {
    entitlements = new Set()

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByRole("link", { name: /Nightly report/ })
    ).toBeInTheDocument()
    expect(formsListForms).not.toHaveBeenCalled()
    expect(
      screen.getByText(/Forms aren.t listed because your plan/)
    ).toBeInTheDocument()
  })

  it("says why forms are missing when the plan lacks them and there are no triggers", async () => {
    entitlements = new Set()
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(await screen.findByText("No triggers yet")).toBeInTheDocument()
    expect(
      screen.getByText(/Forms aren.t listed because your plan/)
    ).toBeInTheDocument()
  })

  it("doesn't claim there are no triggers when the plan check fails", async () => {
    entitlementsFailed = true
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(await screen.findByText("Couldn't load forms.")).toBeInTheDocument()
    expect(screen.queryByText("No triggers yet")).not.toBeInTheDocument()
    expect(formsListForms).not.toHaveBeenCalled()
  })

  it("says nothing about the plan without form:read", async () => {
    entitlements = new Set()
    scopes = { ...scopes, "form:read": false }

    renderWithClient(<WorkspaceTriggers />)

    await screen.findByRole("link", { name: /Nightly report/ })
    expect(
      screen.queryByText(/Forms aren.t listed because your plan/)
    ).not.toBeInTheDocument()
  })

  it("lists no form whose workflow the caller can't see", async () => {
    // form:read alone: the list hides the workflow and the Forms page 404s.
    jest.mocked(formsListForms).mockResolvedValue({
      items: [{ ...FORM, workflow_id: null, workflow_title: null }],
      has_more: false,
    })

    renderWithClient(<WorkspaceTriggers />)

    await screen.findByRole("link", { name: /Nightly report/ })
    expect(formsListForms).toHaveBeenCalled()
    expect(
      screen.queryByRole("link", { name: /Report phishing/ })
    ).not.toBeInTheDocument()
  })

  it("shows the forms error, not the empty state, when there are no triggers", async () => {
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [], has_more: false })
    jest.mocked(formsListForms).mockRejectedValue(new Error("boom"))

    renderWithClient(<WorkspaceTriggers />)

    expect(await screen.findByText("Couldn't load forms.")).toBeInTheDocument()
    expect(screen.queryByText("No triggers yet")).not.toBeInTheDocument()
  })

  it("keeps the forms when triggers fail to load", async () => {
    jest.mocked(triggersListTriggers).mockRejectedValue(TRIGGERS_DENIED)
    jest
      .mocked(formsListForms)
      .mockResolvedValue({ items: [FORM], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByRole("link", { name: /Report phishing/ })
    ).toBeInTheDocument()
    expect(screen.getByText(/Couldn't load all triggers/)).toBeInTheDocument()
  })

  it("says both failed when neither list loads", async () => {
    jest.mocked(triggersListTriggers).mockRejectedValue(TRIGGERS_DENIED)
    jest.mocked(formsListForms).mockRejectedValue(new Error("boom"))

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByText(/Couldn't load triggers\..*Couldn't load forms\./)
    ).toBeInTheDocument()
  })

  it("keeps the triggers when forms fail to load", async () => {
    jest.mocked(formsListForms).mockRejectedValue(new Error("boom"))

    renderWithClient(<WorkspaceTriggers />)

    expect(await screen.findByText("Couldn't load forms.")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /Nightly report/ })
    ).toBeInTheDocument()
  })
})

describe("CreateTriggerDialog kind picker", () => {
  it("offers no form kind", () => {
    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    expect(screen.getByRole("button", { name: /Schedule/ })).toBeEnabled()
    expect(
      screen.queryByRole("button", { name: /^Form/ })
    ).not.toBeInTheDocument()
  })
})
