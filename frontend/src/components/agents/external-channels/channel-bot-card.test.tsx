import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { AgentChannelBotRead } from "@/client"
import {
  api,
  apiError,
  reservedSlackBot,
  serveBots,
  serveEntitlements,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import {
  openProvider,
  type ProviderTitle,
  renderPanel,
} from "@/components/agents/external-channels/channel-bot-test-render"
import { toast } from "@/components/ui/use-toast"

const mockUseScopeCheck = jest.fn<boolean, [string]>(() => true)

jest.mock("@/client")
jest.mock("@/components/ui/use-toast", () => ({
  ...jest.requireActual("@/components/ui/use-toast"),
  toast: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

type BotFactory = (
  overrides?: Partial<AgentChannelBotRead>
) => AgentChannelBotRead

const PROVIDERS: Array<{
  title: ProviderTitle
  installed: BotFactory
  reserved: BotFactory
}> = [
  {
    title: "Custom Slack app",
    installed: slackBot,
    reserved: reservedSlackBot,
  },
]

beforeEach(() => {
  jest.clearAllMocks()
  mockUseScopeCheck.mockReturnValue(true)
  serveEntitlements()
  serveBots([])
})

function confirmation(): HTMLElement {
  return screen.getByRole("alertdialog")
}

describe.each(PROVIDERS)(
  "$title lifecycle",
  ({ title, installed, reserved }) => {
    const bot = installed()

    it("opens no dialog when reserving a bot fails", async () => {
      api.createBot.mockRejectedValue(
        apiError(400, { detail: "No slots left" })
      )
      renderPanel()
      await openProvider(title, "Connect")

      await waitFor(() =>
        expect(toast).toHaveBeenCalledWith(
          expect.objectContaining({
            description: "No slots left",
            variant: "destructive",
          })
        )
      )
      expect(screen.queryByRole("dialog")).toBeNull()
    })

    it("reuses the reserved bot when the dialog opens again", async () => {
      api.createBot.mockResolvedValue(reserved())
      renderPanel()
      await openProvider(title, "Connect")
      expect(await screen.findByRole("dialog")).toBeInTheDocument()
      await userEvent.keyboard("{Escape}")
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())

      await openProvider(title, "Connect")
      expect(await screen.findByRole("dialog")).toBeInTheDocument()
      expect(api.createBot).toHaveBeenCalledTimes(1)
    })

    it("pauses an active bot", async () => {
      serveBots([bot])
      api.updateBot.mockResolvedValue(installed({ status: "paused" }))
      renderPanel()
      const row = await screen.findByRole("group", { name: title })
      expect(within(row).getByText("Active")).toBeInTheDocument()
      await openProvider(title, "Manage")

      await userEvent.click(screen.getByRole("button", { name: "Pause" }))
      expect(api.updateBot).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        botId: bot.id,
        requestBody: { status: "paused" },
      })
    })

    it("resumes a paused bot", async () => {
      serveBots([installed({ status: "paused" })])
      api.updateBot.mockResolvedValue(bot)
      renderPanel()
      await openProvider(title, "Manage")

      await userEvent.click(screen.getByRole("button", { name: "Resume" }))
      expect(api.updateBot).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        botId: bot.id,
        requestBody: { status: "active" },
      })
    })

    it("disconnects only once confirmed, then closes the dialog", async () => {
      serveBots([bot])
      api.disconnectBot.mockResolvedValue(installed({ status: "disconnected" }))
      renderPanel()
      await openProvider(title, "Manage")

      await userEvent.click(screen.getByRole("button", { name: "Disconnect" }))
      expect(confirmation()).toHaveAccessibleName(/disconnect/i)
      await userEvent.click(
        within(confirmation()).getByRole("button", { name: "Cancel" })
      )
      expect(api.disconnectBot).not.toHaveBeenCalled()

      await userEvent.click(screen.getByRole("button", { name: "Disconnect" }))
      await userEvent.click(
        within(confirmation()).getByRole("button", { name: "Disconnect" })
      )
      expect(api.disconnectBot).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        botId: bot.id,
      })
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    })

    it.each([
      { bots: [], action: "Connect" },
      { bots: [bot], action: "Manage" },
    ])("disables $action without agent:update", async ({ bots, action }) => {
      mockUseScopeCheck.mockImplementation((scope) => scope !== "agent:update")
      serveBots(bots)
      renderPanel()
      const row = await screen.findByRole("group", { name: title })

      expect(within(row).getByRole("button", { name: action })).toBeDisabled()
    })

    it("flags a bot that needs reconnecting", async () => {
      serveBots([installed({ status: "needs_reconnect" })])
      renderPanel()
      const row = await screen.findByRole("group", { name: title })

      expect(within(row).getByText("Needs reconnect")).toBeInTheDocument()
      expect(screen.getByRole("alert")).toHaveTextContent(/reconnect/i)
    })
  }
)
