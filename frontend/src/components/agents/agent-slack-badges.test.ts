import { slackPlacementLabel } from "@/components/agents/agent-slack-badges"

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
