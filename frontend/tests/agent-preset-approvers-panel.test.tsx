import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  type AgentPresetApproverRead,
  agentPresetsAddAgentPresetApprovers,
  agentPresetsListAgentPresetApproverOptions,
  agentPresetsListAgentPresetApprovers,
  agentPresetsRemoveAgentPresetApprovers,
} from "@/client"
import { AgentPresetApproversPanel } from "@/components/agents/agent-preset-rail-approvers"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentPresetsListAgentPresetApprovers: jest.fn(),
  agentPresetsListAgentPresetApproverOptions: jest.fn(),
  agentPresetsAddAgentPresetApprovers: jest.fn(),
  agentPresetsRemoveAgentPresetApprovers: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const listApprovers = jest.mocked(agentPresetsListAgentPresetApprovers)
const listOptions = jest.mocked(agentPresetsListAgentPresetApproverOptions)
const addApprovers = jest.mocked(agentPresetsAddAgentPresetApprovers)
const removeApprovers = jest.mocked(agentPresetsRemoveAgentPresetApprovers)

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
  listApprovers.mockResolvedValue({ items: [] } as never)

  renderPanel()

  expect(
    await screen.findByText(
      "No approvers listed: anyone who can reach an approval, here or in the agent's channel, can decide."
    )
  ).toBeInTheDocument()
  expect(listOptions).not.toHaveBeenCalled()
})

it("adds a picked group at once", async () => {
  listApprovers
    .mockResolvedValueOnce({ items: [person] } as never)
    .mockResolvedValue({ items: [person, oncall] } as never)
  listOptions.mockResolvedValue({ items: [person, oncall] } as never)
  addApprovers.mockResolvedValue([oncall] as never)
  const user = userEvent.setup()

  renderPanel()
  await screen.findByText("Example person")
  await user.click(screen.getByRole("button", { name: "Add approver" }))
  await user.click(await screen.findByText("Example on-call"))

  await waitFor(() =>
    expect(addApprovers).toHaveBeenCalledWith({
      workspaceId: "workspace-example",
      presetId: "preset-example",
      requestBody: { approvers: [{ kind: "group", id: "group-example" }] },
    })
  )
  expect(await screen.findByText("Group")).toBeInTheDocument()
})

it("never offers an already-listed approver again", async () => {
  listApprovers.mockResolvedValue({ items: [person] } as never)
  listOptions.mockResolvedValue({ items: [person, oncall] } as never)
  const user = userEvent.setup()

  renderPanel()
  await screen.findByText("Example person")
  await user.click(screen.getByRole("button", { name: "Add approver" }))
  const listed = (await screen.findByText("Added")).closest("[cmdk-item]")
  expect(listed).toHaveAttribute("aria-disabled", "true")
  await user.click(listed as HTMLElement)

  expect(addApprovers).not.toHaveBeenCalled()
})

it("searches approver options on the server", async () => {
  listApprovers.mockResolvedValue({ items: [] } as never)
  listOptions.mockResolvedValue({ items: [oncall] } as never)
  const user = userEvent.setup()

  renderPanel()
  await screen.findByText(/No approvers listed/)
  await user.click(screen.getByRole("button", { name: "Add approver" }))
  await user.type(
    await screen.findByPlaceholderText("Search people, groups, and roles..."),
    "on-call"
  )

  await waitFor(() =>
    expect(listOptions).toHaveBeenLastCalledWith({
      workspaceId: "workspace-example",
      search: "on-call",
      cursor: null,
    })
  )
  expect(await screen.findByText("Example on-call")).toBeInTheDocument()
})

it("removes only the clicked approver", async () => {
  listApprovers.mockResolvedValue({ items: [person, oncall] } as never)
  removeApprovers.mockResolvedValue(undefined as never)
  const user = userEvent.setup()

  renderPanel()
  await user.click(
    await screen.findByRole("button", { name: "Remove Example person" })
  )

  await waitFor(() =>
    expect(removeApprovers).toHaveBeenCalledWith({
      workspaceId: "workspace-example",
      presetId: "preset-example",
      requestBody: { approvers: [{ kind: "user", id: "user-example" }] },
    })
  )
  expect(addApprovers).not.toHaveBeenCalled()
})

it("loads more listed approvers a page at a time", async () => {
  listApprovers
    .mockResolvedValueOnce({
      items: [person],
      has_more: true,
      next_cursor: "page-2",
    } as never)
    .mockResolvedValueOnce({ items: [oncall] } as never)
  const user = userEvent.setup()

  renderPanel()
  await screen.findByText("Example person")
  await user.click(screen.getByRole("button", { name: "Load more" }))

  expect(await screen.findByText("Example on-call")).toBeInTheDocument()
  expect(listApprovers).toHaveBeenLastCalledWith({
    workspaceId: "workspace-example",
    presetId: "preset-example",
    cursor: "page-2",
  })
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull()
})

it("says when the listed approvers could not load", async () => {
  listApprovers.mockRejectedValue(
    Object.assign(new Error("denied"), { status: 403 })
  )

  renderPanel()

  expect(
    await screen.findByText("Approvers could not be loaded.")
  ).toBeInTheDocument()
})

it("asks for a save before an unsaved agent can list approvers", () => {
  renderPanel(null)

  expect(screen.getByRole("button", { name: "Add approver" })).toBeDisabled()
  expect(listApprovers).not.toHaveBeenCalled()
})
