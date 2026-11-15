import { screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  AgentsCatalogViewMode,
  AgentsCatalogViewToggle,
} from "@/components/agents/agents-catalog-view-toggle"
import { useAgentsChannelsHref } from "@/components/agents/external-channels/agents-channels-href"
import {
  api,
  serveBots,
  serveEntitlements,
  WORKSPACE_ID,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderWithClient } from "@/components/agents/external-channels/channel-bot-test-render"
import { useSlackPlatformAppAvailable } from "@/hooks/use-agent-channel-bots"

jest.mock("@/client")

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([])
})

function hrefOf() {
  let href: string | undefined
  let loading = true
  function Probe(): ReactNode {
    href = useAgentsChannelsHref(WORKSPACE_ID)
    loading = useSlackPlatformAppAvailable(WORKSPACE_ID).slackPlatformIsLoading
    return null
  }
  renderWithClient(<Probe />)
  const read = () => href
  // Settled, so an undefined href is the answer rather than a pending query.
  read.settled = () => waitFor(() => expect(loading).toBe(false))
  return read
}

describe("useAgentsChannelsHref", () => {
  it("links the Channels page when the deployment registers the Slack app", async () => {
    api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
    const href = hrefOf()
    await waitFor(() =>
      expect(href()).toBe("/workspaces/workspace-1/agents/channels")
    )
  })

  it("is undefined without a registered Slack app", async () => {
    const href = hrefOf()
    await waitFor(() => expect(api.listPlatformApps).toHaveBeenCalled())
    await href.settled()
    expect(href()).toBeUndefined()
  })

  it("is undefined without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
    const href = hrefOf()
    await waitFor(() =>
      expect(
        jest.requireMock("@/client").organizationGetOrganizationEntitlements
      ).toHaveBeenCalled()
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(href()).toBeUndefined()
    expect(api.listPlatformApps).not.toHaveBeenCalled()
  })
})

describe("AgentsCatalogViewToggle", () => {
  it("shows Channels beside Agents and tags only when given a link", () => {
    const { rerender } = renderWithClient(
      <AgentsCatalogViewToggle
        view={AgentsCatalogViewMode.Channels}
        agentsHref="/a"
        tagsHref="/t"
        channelsHref="/c"
      />
    )
    expect(screen.getByRole("link", { name: "Channels view" })).toHaveAttribute(
      "aria-current",
      "page"
    )
    rerender(
      <AgentsCatalogViewToggle
        view={AgentsCatalogViewMode.Agents}
        agentsHref="/a"
        tagsHref="/t"
      />
    )
    expect(screen.queryByRole("link", { name: "Channels view" })).toBeNull()
  })
})
