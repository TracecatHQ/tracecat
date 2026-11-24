import { fireEvent, render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import { FormProvider, useForm, useWatch } from "react-hook-form"
import type {
  AgentPresetChannelToolGroup,
  RegistryActionReadMinimal,
} from "@/client"
import {
  AgentPresetRailToolGroup,
  AgentPresetRailTools,
  channelGroupKey,
} from "@/components/agents/agent-preset-rail-tools"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { PresetToolFields } from "@/lib/agent-preset-tools"

jest.mock("@/components/tools/tool-picker-dialog", () => ({
  ToolPickerDialog: () => null,
}))

const POST = "internal.slack.post_message"
const LIST = "internal.slack.list_channels"

const SLACK_TOOLS: AgentPresetChannelToolGroup = {
  provider: "slack",
  title: "Slack",
  label: "as @Tracecat",
  supersedes: ["tools.slack"],
  tools: [
    {
      name: LIST,
      title: "List channels",
      description: "List channels.",
      requires_approval: false,
    },
    {
      name: POST,
      title: "Post message",
      description: "Post a message.",
      requires_approval: false,
    },
  ],
}

function registryAction(action: string): RegistryActionReadMinimal {
  return {
    id: action,
    name: action.split(".").at(-1) ?? action,
    description: `${action} description`,
    namespace: action.split(".").slice(0, -1).join("."),
    type: "udf",
    origin: "tracecat",
    action,
  }
}

const REGISTRY = [
  registryAction("tools.slack.post_message"),
  registryAction("core.http_request"),
]

let approvals: PresetToolFields["toolApprovals"] = []

function Approvals() {
  approvals = useWatch({ name: "toolApprovals" })
  return null
}

function Form({
  children,
  toolApprovals = [],
}: {
  children: ReactNode
  toolApprovals?: PresetToolFields["toolApprovals"]
}) {
  const form = useForm({
    defaultValues: {
      actions: ["tools.slack.post_message", "core.http_request"],
      mcpIntegrations: [],
      namespaces: [],
      toolApprovals,
      toolTunnels: {},
    },
  })
  return (
    <TooltipProvider>
      <FormProvider {...form}>
        {children}
        <Approvals />
      </FormProvider>
    </TooltipProvider>
  )
}

function renderTools(
  channelTools: AgentPresetChannelToolGroup[],
  onOpenGroup = jest.fn(),
  toolApprovals: PresetToolFields["toolApprovals"] = []
) {
  render(
    <Form toolApprovals={toolApprovals}>
      <AgentPresetRailTools
        registryActions={REGISTRY}
        mcpIntegrations={[]}
        registryLoading={false}
        mcpLoading={false}
        toolsLoadError={false}
        isSaving={false}
        channelTools={channelTools}
        onOpenGroup={onOpenGroup}
      />
    </Form>
  )
  return onOpenGroup
}

describe("AgentPresetRailTools channel tools", () => {
  it("hides From channels and keeps tools.slack usable when not connected", () => {
    renderTools([])

    expect(screen.queryByText("From channels")).toBeNull()
    expect(screen.queryByTestId("superseded-info")).toBeNull()
    expect(
      screen.getByRole("button", { name: /tools\.slack/ })
    ).not.toHaveAttribute("aria-describedby")
  })

  it("lists the connection's tools and greys the superseded group", () => {
    const onOpenGroup = renderTools([SLACK_TOOLS])

    expect(screen.getByText("From channels")).toBeInTheDocument()
    const group = screen.getByRole("button", { name: /as @Tracecat/ })
    expect(within(group).getByText("2 tools")).toBeInTheDocument()
    fireEvent.click(group)
    expect(onOpenGroup).toHaveBeenCalledWith(
      channelGroupKey("slack"),
      "Slack · as @Tracecat"
    )

    // The secret-based group stays selected, greyed, and says why.
    const superseded = screen.getByRole("button", { name: /tools\.slack/ })
    expect(superseded).toHaveAccessibleDescription(
      "Not used while this agent is connected to Slack. It uses From channels instead."
    )
    expect(within(superseded).getByText("1 of 1")).toBeInTheDocument()
    expect(
      within(superseded).getByTestId("superseded-info")
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /^Core core/ })
    ).not.toHaveAttribute("aria-describedby")
  })
})

describe("AgentPresetRailTools approval count", () => {
  const rules = [{ tool: "tools.slack.post_message", allow: true }]

  it("counts a superseded group's asks only while not connected", () => {
    renderTools([], jest.fn(), rules)
    expect(screen.getByText("1 needs approval")).toBeInTheDocument()
  })

  it("drops a superseded group's asks while connected", () => {
    renderTools([SLACK_TOOLS], jest.fn(), rules)
    expect(screen.queryByText(/needs? approval/)).toBeNull()
  })
})

describe("AgentPresetRailToolGroup channel drill-in", () => {
  function renderGroup(toolApprovals: PresetToolFields["toolApprovals"] = []) {
    render(
      <Form toolApprovals={toolApprovals}>
        <AgentPresetRailToolGroup
          groupKey={channelGroupKey("slack")}
          registryActions={REGISTRY}
          mcpIntegrations={[]}
          registryLoading={false}
          mcpLoading={false}
          toolsLoadError={false}
          isSaving={false}
          channelTools={[SLACK_TOOLS]}
        />
      </Form>
    )
  }

  it("writes Ask and Auto choices to tool_approvals, with no add or remove", () => {
    renderGroup()

    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull()
    expect(screen.queryByRole("button", { name: "Add tools" })).toBeNull()
    expect(
      screen.getByText("Only in conversations started in Slack.")
    ).toBeInTheDocument()

    fireEvent.click(
      screen.getByRole("button", { name: `Require approval for ${POST}` })
    )
    expect(approvals).toEqual([{ tool: POST, allow: true }])

    fireEvent.click(screen.getByRole("button", { name: "Needs approval" }))
    expect(approvals).toEqual([
      { tool: POST, allow: true },
      { tool: LIST, allow: true },
    ])

    // Back to the plugin's default: no rule is stored.
    fireEvent.click(screen.getByRole("button", { name: "Auto" }))
    expect(approvals).toEqual([])
  })

  it("shows a saved rule as Needs approval", () => {
    renderGroup([{ tool: LIST, allow: true }])

    const row = screen.getByText("List channels").closest("div") as HTMLElement
    expect(within(row).getByText("Needs approval")).toBeInTheDocument()
  })
})
