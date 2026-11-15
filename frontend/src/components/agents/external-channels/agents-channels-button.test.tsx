import { screen, waitFor } from "@testing-library/react"
import { AgentsChannelsButton } from "@/components/agents/external-channels/agents-channels-button"
import {
  api,
  serveBots,
  serveEntitlements,
  WORKSPACE_ID,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { renderWithClient } from "@/components/agents/external-channels/channel-bot-test-render"

jest.mock("@/client")

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([])
})

describe("AgentsChannelsButton", () => {
  it("opens the Channels page when the deployment registers the Slack app", async () => {
    api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
    renderWithClient(<AgentsChannelsButton workspaceId={WORKSPACE_ID} />)
    expect(
      await screen.findByRole("link", { name: "Channels" })
    ).toHaveAttribute("href", "/workspaces/workspace-1/agents/channels")
  })

  it("is hidden without a registered Slack app", async () => {
    renderWithClient(<AgentsChannelsButton workspaceId={WORKSPACE_ID} />)
    await waitFor(() => expect(api.listPlatformApps).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole("link", { name: "Channels" })).toBeNull()
  })

  it("is hidden without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    api.listPlatformApps.mockResolvedValue([{ provider: "slack" }])
    renderWithClient(<AgentsChannelsButton workspaceId={WORKSPACE_ID} />)
    await waitFor(() =>
      expect(
        jest.requireMock("@/client").organizationGetOrganizationEntitlements
      ).toHaveBeenCalled()
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole("link", { name: "Channels" })).toBeNull()
    expect(api.listPlatformApps).not.toHaveBeenCalled()
  })
})
