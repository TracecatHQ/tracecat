import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  type AgentPresetApproverRead,
  agentPresetsListAgentPresetApproverOptions,
  agentPresetsListAgentPresetApprovers,
  agentPresetsReplaceAgentPresetApprovers,
} from "@/client"
import { AgentPresetApproversPanel } from "@/components/agents/agent-preset-rail-approvers"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentPresetsListAgentPresetApprovers: jest.fn(),
  agentPresetsListAgentPresetApproverOptions: jest.fn(),
  agentPresetsReplaceAgentPresetApprovers: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const listApprovers = jest.mocked(agentPresetsListAgentPresetApprovers)
const listOptions = jest.mocked(agentPresetsListAgentPresetApproverOptions)
const replaceApprovers = jest.mocked(agentPresetsReplaceAgentPresetApprovers)

const person: AgentPresetApproverRead = {
  kind: "user",
  id: "user-example",
  name: "Example person",
  email: "person@example.com",
}
const oncall: AgentPresetApproverRead = {
  kind: "group",
  id: "group-example",
  name: "Example on-call",
}

function renderPanel(presetId: string | null = "preset-example") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AgentPresetApproversPanel
          workspaceId="workspace-example"
          presetId={presetId}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
})

it("explains the default rule while no approvers are listed", async () => {
  listApprovers.mockResolvedValue([] as never)

  renderPanel()

  expect(
    await screen.findByText(
      "No approvers listed: anyone who can reach an approval, here or in the agent's channel, can decide."
    )
  ).toBeInTheDocument()
  expect(listOptions).not.toHaveBeenCalled()
})

it("adds a group from the picker and saves the full set", async () => {
  listApprovers.mockResolvedValue([person] as never)
  listOptions.mockResolvedValue([person, oncall] as never)
  replaceApprovers.mockResolvedValue([person, oncall] as never)
  const user = userEvent.setup()

  renderPanel()
  await screen.findByText("Example person")
  await user.click(screen.getByRole("button", { name: "Add approver" }))
  await user.click(await screen.findByText("Example on-call"))

  await waitFor(() =>
    expect(replaceApprovers).toHaveBeenCalledWith({
      workspaceId: "workspace-example",
      presetId: "preset-example",
      requestBody: {
        approvers: [
          { kind: "user", id: "user-example" },
          { kind: "group", id: "group-example" },
        ],
      },
    })
  )
  expect(await screen.findByText("Group")).toBeInTheDocument()
})

it("removes an approver", async () => {
  listApprovers.mockResolvedValue([person, oncall] as never)
  replaceApprovers.mockResolvedValue([oncall] as never)
  const user = userEvent.setup()

  renderPanel()
  await user.click(
    await screen.findByRole("button", { name: "Remove Example person" })
  )

  await waitFor(() =>
    expect(replaceApprovers).toHaveBeenCalledWith(
      expect.objectContaining({
        requestBody: { approvers: [{ kind: "group", id: "group-example" }] },
      })
    )
  )
})

it("blocks edits until the current approvers load", async () => {
  listApprovers.mockRejectedValue(
    Object.assign(new Error("denied"), { status: 403 })
  )

  renderPanel()

  expect(
    await screen.findByText("Approvers could not be loaded.")
  ).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Add approver" })).toBeDisabled()
  expect(replaceApprovers).not.toHaveBeenCalled()
})

it("asks for a save before an unsaved agent can list approvers", () => {
  renderPanel(null)

  expect(screen.getByRole("button", { name: "Add approver" })).toBeDisabled()
  expect(listApprovers).not.toHaveBeenCalled()
})
