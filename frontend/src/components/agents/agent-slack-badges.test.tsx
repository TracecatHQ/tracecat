import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  AgentSlackBadges,
  slackPlacementLabel,
} from "@/components/agents/agent-slack-badges"
import { TooltipProvider } from "@/components/ui/tooltip"

jest.mock("@/client")

describe("slackPlacementLabel", () => {
  const channel = {
    id: "C1",
    name: "alerts",
    is_private: false,
    is_member: true,
  }

  it("names a routed channel with its Enterprise Grid workspace", () => {
    expect(
      slackPlacementLabel({
        kind: "channel",
        channel: { ...channel, team_id: "T1", team_name: "SecOps" },
      })
    ).toBe("SecOps › #alerts")
  })

  it("names a single-workspace channel", () => {
    expect(slackPlacementLabel({ kind: "channel", channel })).toBe("#alerts")
  })
})

describe("AgentSlackBadges", () => {
  it("marks public channels with # and private ones with a lock in the overflow tooltip", async () => {
    const channel = (name: string, isPrivate: boolean) => ({
      kind: "channel" as const,
      channel: { id: name, name, is_private: isPrivate, is_member: true },
    })
    render(
      <TooltipProvider>
        <AgentSlackBadges
          placements={[
            channel("a", false),
            channel("b", false),
            channel("channel1", false),
            channel("channel2", true),
          ]}
        />
      </TooltipProvider>
    )
    await userEvent.hover(screen.getByText("+2"))
    const tooltip = await screen.findByRole("tooltip")
    const [publicItem, privateItem] = within(tooltip).getAllByRole("listitem")
    expect(publicItem).toHaveTextContent("#channel1")
    expect(within(publicItem).queryByLabelText("Private channel")).toBeNull()
    expect(privateItem).toHaveTextContent("channel2")
    expect(
      within(privateItem).getByLabelText("Private channel")
    ).toBeInTheDocument()
  })

  it("asks for a reinstall from the overflow tooltip", async () => {
    const channel = (id: string) => ({
      kind: "channel" as const,
      channel: { id, name: id, is_private: false, is_member: true },
    })
    render(
      <TooltipProvider>
        <AgentSlackBadges
          placements={[
            channel("a"),
            channel("b"),
            { kind: "workspace_bot", botId: "own-1", needsReinstall: true },
          ]}
        />
      </TooltipProvider>
    )
    await userEvent.hover(screen.getByText("+1"))
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Reinstall to show channels"
    )
  })
})
