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
        channelId: "C1",
        channel: { ...channel, team_id: "T1", team_name: "SecOps" },
      })
    ).toBe("SecOps › #alerts")
  })

  it("names a single-workspace channel, and an unlisted one by ID", () => {
    expect(
      slackPlacementLabel({ kind: "channel", channelId: "C1", channel })
    ).toBe("#alerts")
    expect(
      slackPlacementLabel({ kind: "channel", channelId: "C9", channel: null })
    ).toBe("C9")
  })
})
