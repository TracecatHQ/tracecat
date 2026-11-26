import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type EventSourceRead,
  type TriggerRead,
  triggersCreateEventSource,
  triggersCreateTrigger,
  triggersGetEventSource,
  triggersGetTrigger,
  triggersListEventSources,
  triggersListTriggers,
  triggersUpdateTriggerBinding,
} from "@/client"
import { CreateTriggerDialog } from "@/components/triggers/create-trigger-dialog"
import { TriggerDetail } from "@/components/triggers/trigger-detail"
import { WorkspaceTriggers } from "@/components/triggers/workspace-triggers"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  triggersCreateEventSource: jest.fn(),
  triggersCreateTrigger: jest.fn(),
  triggersGetEventSource: jest.fn(),
  triggersGetTrigger: jest.fn(),
  triggersListEventSources: jest.fn(),
  triggersListTriggers: jest.fn(),
  triggersUpdateTriggerBinding: jest.fn(),
}))

let scopes: Record<string, boolean | undefined> = {}
let entitled = true
const push = jest.fn()

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes[scope],
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => key === "object_triggers" && entitled,
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
// The add-target dialog's pickers; not opened here.
jest.mock("@/lib/hooks", () => ({
  useWorkflowManager: () => ({ workflows: [], workflowsLoading: false }),
}))
jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPresets: () => ({ presets: [], presetsIsLoading: false }),
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
      screen.getByText(/A trigger listens for outside events/)
    ).toBeInTheDocument()
  })

  it("shows the upgrade state and fetches nothing without the entitlement", () => {
    entitled = false

    renderWithClient(<WorkspaceTriggers />)

    expect(screen.getByText("Upgrade required")).toBeInTheDocument()
    expect(triggersListTriggers).not.toHaveBeenCalled()
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

  it("names the missing execute scope when a target can't go online", async () => {
    const user = userEvent.setup()
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
