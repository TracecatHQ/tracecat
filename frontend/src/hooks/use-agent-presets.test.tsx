import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type AgentPresetChannelToolGroup,
  agentPresetsListAgentPresetChannelTools,
} from "@/client"
import { serveEntitlements } from "@/components/agents/external-channels/channel-bot-fixtures"
import { useAgentPresetChannelTools } from "@/hooks/use-agent-presets"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client")

const SLACK = {
  provider: "slack",
  title: "Slack",
  label: "as @Tracecat",
  tools: [{ name: "internal.slack.read_thread", title: "Read thread" }],
  supersedes: ["tools.slack"],
} as unknown as AgentPresetChannelToolGroup

describe("useAgentPresetChannelTools", () => {
  it("drops cached channel tools once the preview entitlement is gone", async () => {
    serveEntitlements()
    jest
      .mocked(agentPresetsListAgentPresetChannelTools)
      .mockResolvedValue({ groups: [SLACK] })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(
      () => useAgentPresetChannelTools("workspace-1", "preset-1"),
      { wrapper }
    )
    await waitFor(() => expect(result.current).toEqual([SLACK]))

    serveEntitlements({ agent_channels: true })
    await act(async () => {
      await client.invalidateQueries()
    })

    await waitFor(() => expect(result.current).toEqual([]))
  })
})
