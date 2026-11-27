import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type EventSourceRead,
  type TriggerRead,
  triggersCreateEventSource,
  triggersCreateTrigger,
  triggersCreateTriggerBinding,
  triggersGetEventSource,
  triggersGetTrigger,
  triggersListEventSources,
  triggersListTriggers,
  triggersUpdateTriggerBinding,
} from "@/client"
import { AddTargetDialog } from "@/components/triggers/add-target-dialog"
import { CreateTriggerDialog } from "@/components/triggers/create-trigger-dialog"
import { shellQuote } from "@/components/triggers/object-storage/event-source-panel"
import { TriggerDetail } from "@/components/triggers/trigger-detail"
import { WorkspaceTriggers } from "@/components/triggers/workspace-triggers"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  triggersCreateEventSource: jest.fn(),
  triggersCreateTrigger: jest.fn(),
  triggersCreateTriggerBinding: jest.fn(),
  triggersGetEventSource: jest.fn(),
  triggersGetTrigger: jest.fn(),
  triggersListEventSources: jest.fn(),
  triggersListTriggers: jest.fn(),
  triggersUpdateTriggerBinding: jest.fn(),
}))

let scopes: Record<string, boolean | undefined> = {}
let entitled = true
let entitlementsFailed = false
const push = jest.fn()

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes[scope],
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) =>
      !entitlementsFailed && key === "object_triggers" && entitled,
    hasEntitlementData: !entitlementsFailed,
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
// The add-target dialog's pickers.
jest.mock("@/lib/hooks", () => ({
  useWorkflowManager: () => ({ workflows: [], workflowsLoading: false }),
}))
let presets: { id: string; name: string; description: string | null }[] = []
jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: () => ({ presets, presetsIsLoading: false }),
}))
jest.mock("@/components/loading/spinner", () => ({
  CenteredSpinner: () => <div>Loading</div>,
}))

const SOURCE: EventSourceRead = {
  id: "source-1",
  kind: "aws_sns",
  name: "Uploads",
  config: {
    kind: "aws_sns",
    topic_arn: "arn:aws:sns:us-east-1:123456789012:uploads",
  },
  status: "online",
  subscription_status: "pending",
  subscription_ref: null,
  endpoint_url: "https://tracecat.example.com/api/ingest/sns/token",
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
}

const TRIGGER: TriggerRead = {
  id: "trigger-1",
  kind: "object_storage",
  name: "New uploads",
  status: "offline",
  config: {
    kind: "object_storage",
    source_id: SOURCE.id,
    source_name: SOURCE.name,
    key_prefix: "incoming/",
  },
  bindings: [
    {
      id: "binding-1",
      trigger_id: "trigger-1",
      target_type: "workflow",
      target_id: "6f1c2a7e-3b4d-4e5f-9a1b-2c3d4e5f6a7b",
      target_name: "Scan file",
      instruction: null,
      status: "offline",
      created_at: "2026-11-27T00:00:00Z",
      updated_at: "2026-11-27T00:00:00Z",
    },
    {
      id: "binding-2",
      trigger_id: "trigger-1",
      target_type: "agent",
      target_id: "preset-1",
      target_name: "Triage agent",
      instruction: "Summarize the file",
      status: "online",
      created_at: "2026-11-27T00:00:00Z",
      updated_at: "2026-11-27T00:00:00Z",
    },
  ],
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
}

const SCHEDULE_TRIGGER: TriggerRead = {
  id: "trigger-2",
  kind: "schedule",
  name: "Nightly report schedule",
  status: "online",
  config: {
    kind: "schedule",
    workflow_id: "wf_3nF1fBrbY6AIX7Mr2qhEpR",
    schedule_id: "sch_1",
    cron: "0 9 * * *",
    timeout: 0,
  },
  bindings: [
    {
      id: "binding-3",
      trigger_id: "trigger-2",
      target_type: "workflow",
      target_id: "6f1c2a7e-3b4d-4e5f-9a1b-2c3d4e5f6a7b",
      target_name: "Nightly report",
      instruction: null,
      status: "online",
      created_at: "2026-11-27T00:00:00Z",
      updated_at: "2026-11-27T00:00:00Z",
    },
  ],
  created_at: "2026-11-27T00:00:00Z",
  updated_at: "2026-11-27T00:00:00Z",
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

beforeEach(() => {
  jest.clearAllMocks()
  entitled = true
  entitlementsFailed = false
  presets = []
  scopes = {
    "trigger:read": true,
    "trigger:create": true,
    "trigger:update": true,
    "trigger:delete": true,
  }
})

describe("WorkspaceTriggers", () => {
  it("lists each trigger with its kind, source, targets and status", async () => {
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [TRIGGER], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    const row = await screen.findByRole("link", { name: /New uploads/ })
    expect(row).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/triggers/trigger-1"
    )
    expect(row).toHaveTextContent("Object storage")
    expect(row).toHaveTextContent("Uploads · incoming/")
    expect(row).toHaveTextContent("Scan file")
    expect(row).toHaveTextContent("Triage agent")
    expect(row).toHaveTextContent("Offline")
  })

  it("explains what a trigger is when there are none", async () => {
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(await screen.findByText("No triggers yet")).toBeInTheDocument()
    expect(
      screen.getByText(/A trigger starts workflows and agents/)
    ).toBeInTheDocument()
  })

  it("loads older triggers on request", async () => {
    const user = userEvent.setup()
    const older = { ...TRIGGER, id: "trigger-0", name: "Older trigger" }
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValueOnce({
        items: [TRIGGER],
        has_more: true,
        next_cursor: "cursor-2",
      })
      .mockResolvedValueOnce({ items: [older], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    await user.click(await screen.findByRole("button", { name: "Load more" }))

    expect(
      await screen.findByRole("link", { name: /Older trigger/ })
    ).toBeInTheDocument()
    expect(triggersListTriggers).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "cursor-2" })
    )
    expect(
      screen.queryByRole("button", { name: "Load more" })
    ).not.toBeInTheDocument()
  })

  it("lists triggers on every plan", async () => {
    entitled = false
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValue({ items: [SCHEDULE_TRIGGER], has_more: false })

    renderWithClient(<WorkspaceTriggers />)

    expect(
      await screen.findByRole("link", { name: /Nightly report schedule/ })
    ).toBeInTheDocument()
    expect(screen.getByText("0 9 * * *")).toBeInTheDocument()
    expect(screen.getByText("Schedule")).toBeInTheDocument()
  })

  it("keeps the list when a later page fails", async () => {
    const user = userEvent.setup()
    jest
      .mocked(triggersListTriggers)
      .mockResolvedValueOnce({
        items: [TRIGGER],
        has_more: true,
        next_cursor: "cursor-2",
      })
      .mockRejectedValueOnce(
        // A 4xx: the shared retry handler doesn't retry it.
        Object.assign(new Error("Bad cursor"), {
          status: 400,
          body: { detail: "Bad cursor" },
        })
      )

    renderWithClient(<WorkspaceTriggers />)

    await user.click(await screen.findByRole("button", { name: "Load more" }))

    expect(
      await screen.findByText(/Couldn't load all triggers/)
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /New uploads/ })
    ).toBeInTheDocument()
  })
})

describe("CreateTriggerDialog kind picker", () => {
  it("marks the kinds the plan lacks and keeps the others open", () => {
    entitled = false

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    const objectStorage = screen.getByRole("button", { name: /Object storage/ })
    expect(objectStorage).toBeDisabled()
    expect(objectStorage).toHaveTextContent("Upgrade required")
    expect(screen.getByRole("button", { name: /Schedule/ })).toBeEnabled()
  })

  it("says the plan is unknown rather than locked when the check fails", () => {
    entitlementsFailed = true

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    expect(
      screen.getByRole("button", { name: /Object storage/ })
    ).toHaveTextContent("Plan unknown")
    expect(screen.getByRole("button", { name: /Schedule/ })).toBeEnabled()
  })
})

describe("CreateTriggerDialog", () => {
  it("picks a kind, creates a source inline, then the trigger", async () => {
    const user = userEvent.setup()
    jest
      .mocked(triggersListEventSources)
      .mockResolvedValue({ items: [], has_more: false })
    jest.mocked(triggersCreateEventSource).mockResolvedValue(SOURCE)
    jest.mocked(triggersCreateTrigger).mockResolvedValue(TRIGGER)

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await user.click(screen.getByRole("button", { name: /Object storage/ }))
    await user.type(screen.getByLabelText("Name"), "New uploads")
    await user.type(screen.getByLabelText("Event source name"), "Uploads")
    await user.type(
      screen.getByLabelText("SNS topic ARN"),
      SOURCE.config.topic_arn
    )
    await user.type(screen.getByLabelText("Key prefix"), "incoming/")
    await user.click(screen.getByRole("button", { name: "Create trigger" }))

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        "/workspaces/workspace-1/triggers/trigger-1"
      )
    )
    expect(triggersCreateEventSource).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        name: "Uploads",
        config: { kind: "aws_sns", topic_arn: SOURCE.config.topic_arn },
        status: "online",
      },
    })
    expect(triggersCreateTrigger).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        name: "New uploads",
        config: {
          kind: "object_storage",
          source_id: SOURCE.id,
          key_prefix: "incoming/",
        },
      },
    })
  })
})

describe("CreateTriggerDialog name check", () => {
  it("refuses a blank name before creating a source", async () => {
    const user = userEvent.setup()
    jest
      .mocked(triggersListEventSources)
      .mockResolvedValue({ items: [], has_more: false })

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await user.click(screen.getByRole("button", { name: /Object storage/ }))
    await user.type(screen.getByLabelText("Name"), "   ")
    await user.type(screen.getByLabelText("Event source name"), "Uploads")
    await user.type(
      screen.getByLabelText("SNS topic ARN"),
      SOURCE.config.topic_arn
    )
    await user.click(screen.getByRole("button", { name: "Create trigger" }))

    expect(
      await screen.findByText("Give the trigger a name.")
    ).toBeInTheDocument()
    expect(triggersCreateEventSource).not.toHaveBeenCalled()
    expect(triggersCreateTrigger).not.toHaveBeenCalled()
  })
})

describe("CreateTriggerDialog while submitting", () => {
  it("locks the name until the inline source and the trigger are created", async () => {
    const user = userEvent.setup()
    let finishSource: (source: EventSourceRead) => void = () => {}
    jest
      .mocked(triggersListEventSources)
      .mockResolvedValue({ items: [], has_more: false })
    jest.mocked(triggersCreateEventSource).mockReturnValue(
      new Promise<EventSourceRead>((resolve) => {
        finishSource = resolve
      }) as never
    )
    jest.mocked(triggersCreateTrigger).mockResolvedValue(TRIGGER)

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await user.click(screen.getByRole("button", { name: /Object storage/ }))
    await user.type(screen.getByLabelText("Name"), "New uploads")
    await user.type(screen.getByLabelText("Event source name"), "Uploads")
    await user.type(
      screen.getByLabelText("SNS topic ARN"),
      SOURCE.config.topic_arn
    )
    await user.click(screen.getByRole("button", { name: "Create trigger" }))

    await waitFor(() => expect(screen.getByLabelText("Name")).toBeDisabled())
    expect(screen.getByRole("button", { name: "Existing" })).toBeDisabled()
    expect(
      screen.getByRole("button", { name: "Create trigger" })
    ).toBeDisabled()
    expect(triggersCreateTrigger).not.toHaveBeenCalled()

    finishSource(SOURCE)

    await waitFor(() =>
      expect(triggersCreateTrigger).toHaveBeenCalledWith(
        expect.objectContaining({
          requestBody: expect.objectContaining({ name: "New uploads" }),
        })
      )
    )
  })
})

describe("useEventSources via the form", () => {
  // jsdom lacks the pointer capture API that Radix Select calls on open.
  beforeAll(() => {
    Element.prototype.hasPointerCapture = () => false
    Element.prototype.releasePointerCapture = () => {}
  })

  it("offers sources from every page", async () => {
    const user = userEvent.setup()
    const second = {
      ...SOURCE,
      id: "source-2",
      name: "Archive bucket",
      config: {
        kind: "aws_sns" as const,
        topic_arn: "arn:aws:sns:us-east-1:123456789012:archive",
      },
    }
    jest
      .mocked(triggersListEventSources)
      .mockResolvedValueOnce({
        items: [SOURCE],
        has_more: true,
        next_cursor: "cursor-2",
      })
      .mockResolvedValueOnce({ items: [second], has_more: false })

    renderWithClient(<CreateTriggerDialog open onOpenChange={jest.fn()} />)

    await user.click(screen.getByRole("button", { name: /Object storage/ }))
    await waitFor(() =>
      expect(triggersListEventSources).toHaveBeenCalledTimes(2)
    )
    expect(triggersListEventSources).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "cursor-2" })
    )
    await user.click(screen.getByRole("combobox", { name: "Event source" }))
    expect(
      await screen.findByRole("option", { name: /Archive bucket/ })
    ).toBeInTheDocument()
  })
})

describe("TriggerDetail", () => {
  beforeEach(() => {
    jest.mocked(triggersGetTrigger).mockResolvedValue(TRIGGER)
    jest.mocked(triggersGetEventSource).mockResolvedValue(SOURCE)
  })

  it("shows targets, the endpoint URL and the setup steps", async () => {
    renderWithClient(<TriggerDetail triggerId="trigger-1" />)

    expect(
      await screen.findByRole("link", { name: "Scan file" })
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_3nF1fBrbY6AIX7Mr2qhEpR"
    )
    expect(screen.getByRole("link", { name: "Triage agent" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/agents/preset-1"
    )
    expect(
      await screen.findByText(SOURCE.endpoint_url ?? "")
    ).toBeInTheDocument()
    expect(screen.getByText("Subscription pending")).toBeInTheDocument()
    expect(
      screen.getByText(
        `aws sns subscribe --topic-arn '${SOURCE.config.topic_arn}' --protocol https --notification-endpoint '${SOURCE.endpoint_url}'`
      )
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Leave raw message delivery off/)
    ).toBeInTheDocument()
  })

  it("explains why the endpoint URL is hidden", async () => {
    jest
      .mocked(triggersGetEventSource)
      .mockResolvedValue({ ...SOURCE, endpoint_url: null })

    renderWithClient(<TriggerDetail triggerId="trigger-1" />)

    expect(
      await screen.findByText(/Only members who can change triggers/)
    ).toBeInTheDocument()
  })

  it("names the missing execute scope when the API refuses a target", async () => {
    const user = userEvent.setup()
    // The UI allows it; the server still refuses (a stale scope set).
    scopes = { ...scopes, "workflow:execute": true }
    jest.mocked(triggersUpdateTriggerBinding).mockRejectedValue(
      Object.assign(new Error("Forbidden"), {
        status: 403,
        body: {
          error: {
            code: "insufficient_scope",
            missing_scopes: ["workflow:execute"],
          },
        },
      })
    )

    renderWithClient(<TriggerDetail triggerId="trigger-1" />)

    await user.click(
      await screen.findByRole("switch", { name: "Scan file online" })
    )

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Couldn't put the target online",
          description: expect.stringContaining(
            "run workflows (workflow:execute)"
          ),
        })
      )
    )
  })

  it("keeps a target offline without its execute scope", async () => {
    scopes = { ...scopes, "agent:execute": true }

    renderWithClient(<TriggerDetail triggerId="trigger-1" />)

    const workflowSwitch = await screen.findByRole("switch", {
      name: "Scan file online",
    })
    expect(workflowSwitch).toBeDisabled()
    expect(workflowSwitch.parentElement).toHaveAttribute(
      "title",
      "You need permission to run workflows (workflow:execute) to put this target online."
    )
  })

  it("keeps the trigger offline without the execute scope of an online target", async () => {
    scopes = { ...scopes, "workflow:execute": true }

    renderWithClient(<TriggerDetail triggerId="trigger-1" />)

    const triggerSwitch = await screen.findByRole("switch", {
      name: "New uploads online",
    })
    expect(triggerSwitch).toBeDisabled()
    expect(triggerSwitch.parentElement).toHaveAttribute(
      "title",
      "You need permission to run agents (agent:execute) to put this trigger online."
    )
  })

  it("hides add, remove and delete without the scopes", async () => {
    scopes = { "trigger:read": true }

    renderWithClient(<TriggerDetail triggerId="trigger-1" />)

    expect(await screen.findByText("Scan file")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add target" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Remove/ })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Delete/ })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("switch", { name: "New uploads online" })
    ).toBeDisabled()
  })
})

describe("TriggerDetail for a schedule", () => {
  it("shows the schedule and keeps its one target fixed", async () => {
    jest.mocked(triggersGetTrigger).mockResolvedValue(SCHEDULE_TRIGGER)

    renderWithClient(<TriggerDetail triggerId="trigger-2" />)

    expect(
      (await screen.findAllByRole("link", { name: "Nightly report" }))[0]
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_3nF1fBrbY6AIX7Mr2qhEpR"
    )
    expect(screen.getByText("0 9 * * *")).toBeInTheDocument()
    expect(screen.getByText("sch_1")).toBeInTheDocument()
    expect(
      screen.getByText(/A schedule runs the workflow it was created for/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add target" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Remove/ })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("switch", { name: "Nightly report online" })
    ).not.toBeInTheDocument()
    // The trigger itself still goes online and offline, and can be deleted.
    expect(
      screen.getByRole("switch", { name: "Nightly report schedule online" })
    ).toBeEnabled()
    expect(
      screen.getByRole("button", { name: "Delete Nightly report schedule" })
    ).toBeInTheDocument()
  })
})

describe("shellQuote", () => {
  it("keeps a value with shell metacharacters as one literal word", () => {
    expect(shellQuote("a b;$(x)'y")).toBe(`'a b;$(x)'"'"'y'`)
  })
})

describe("AddTargetDialog", () => {
  it("locks every control while the target is being added", async () => {
    const user = userEvent.setup()
    scopes = { ...scopes, "agent:read": true, "agent:execute": true }
    presets = [{ id: "preset-9", name: "Report agent", description: null }]
    jest
      .mocked(triggersCreateTriggerBinding)
      .mockReturnValue(new Promise(() => {}) as never)

    renderWithClient(
      <AddTargetDialog
        workspaceId="workspace-1"
        trigger={TRIGGER}
        open
        onOpenChange={jest.fn()}
      />
    )

    await user.click(screen.getByRole("button", { name: /Agent/ }))
    await user.click(screen.getByText("Report agent"))
    await user.type(screen.getByLabelText("Instruction"), "Summarize it")
    await user.click(screen.getByRole("button", { name: "Add target" }))

    await waitFor(() =>
      expect(screen.getByLabelText("Instruction")).toBeDisabled()
    )
    expect(screen.getByRole("button", { name: /Workflow/ })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Add target" })).toBeDisabled()
    expect(triggersCreateTriggerBinding).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      triggerId: "trigger-1",
      requestBody: {
        target: {
          type: "agent",
          agent_preset_id: "preset-9",
          instruction: "Summarize it",
        },
      },
    })
  })
})
