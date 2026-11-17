import { render, screen } from "@testing-library/react"
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
