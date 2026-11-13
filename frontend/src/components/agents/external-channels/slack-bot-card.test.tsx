import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ChannelBotInstallResult } from "@/client"
import {
  api,
  apiError,
  reservedSlackBot,
  SLACK_ENDPOINT,
  SLACK_REDIRECT,
  serveBots,
  serveEntitlements,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import {
  openProvider,
  renderPanel,
} from "@/components/agents/external-channels/channel-bot-test-render"
import { goToChannelBotAuthorization } from "@/components/agents/external-channels/channel-bot-utils"
import { toast } from "@/components/ui/use-toast"

jest.mock("@/client")
jest.mock("@/components/ui/use-toast", () => ({
  ...jest.requireActual("@/components/ui/use-toast"),
  toast: jest.fn(),
}))
jest.mock("@/components/agents/external-channels/channel-bot-utils", () => ({
  ...jest.requireActual(
    "@/components/agents/external-channels/channel-bot-utils"
  ),
  goToChannelBotAuthorization: jest.fn(),
}))

const goTo = jest.mocked(goToChannelBotAuthorization)
const AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize?client_id=123.456"
const RECONNECT_WARNING = /takes this bot offline/

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([])
})

async function openDialog(action: "Connect" | "Manage"): Promise<void> {
  await openProvider("Custom Slack app", action)
}

async function fillInstallFields(appToken?: string): Promise<void> {
  await userEvent.type(screen.getByLabelText("Client ID"), "123.456")
  await userEvent.type(screen.getByLabelText("Client secret"), "cs")
  await userEvent.type(screen.getByLabelText("Signing secret"), "ss")
  if (appToken) {
    await userEvent.type(screen.getByLabelText("App-level token"), appToken)
  }
}

async function submit(name: string): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name }))
}

describe("SlackBotCard", () => {
  it("reserves a bot and shows its URLs before the first install", async () => {
    const reserved = reservedSlackBot()
    api.createBot.mockResolvedValue(reserved)
    renderPanel()
    await screen.findByText("Custom Slack app")
    serveBots([reserved])
    await openDialog("Connect")

    expect(api.createBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: { agent_preset_id: "preset-1", provider: "slack" },
    })
    expect(await screen.findByLabelText("Endpoint URL")).toHaveValue(
      SLACK_ENDPOINT
    )
    expect(screen.getByLabelText("OAuth redirect URL")).toHaveValue(
      SLACK_REDIRECT
    )
    expect(screen.getByText("Not installed")).toBeInTheDocument()
  })

  it("never asks an HTTP bot for an app-level token", async () => {
    serveBots([reservedSlackBot()])
    renderPanel()
    await openDialog("Connect")
    expect(screen.getByLabelText("Client secret")).toHaveAttribute(
      "autocomplete",
      "new-password"
    )
    expect(screen.queryByLabelText("App-level token")).toBeNull()
  })

  it("installs an HTTP bot with the exact body and goes to Slack", async () => {
    serveBots([reservedSlackBot()])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    await openDialog("Connect")
    await fillInstallFields()
    await submit("Connect to Slack")

    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        provider: "slack",
        bot_id: "bot-1",
        agent_preset_id: "preset-1",
        client_id: "123.456",
        client_secret: "cs",
        signing_secret: "ss",
        return_url: window.location.href,
      },
    })
    await waitFor(() => expect(goTo).toHaveBeenCalledWith(AUTHORIZE_URL))
  })

  it("unmounts the form, and its secrets, before going to Slack", async () => {
    serveBots([reservedSlackBot()])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(),
      authorization_url: AUTHORIZE_URL,
    })
    const onPage: Array<HTMLElement | null> = []
    goTo.mockImplementation(() => {
      onPage.push(screen.queryByDisplayValue("cs"))
    })
    renderPanel()
    await openDialog("Connect")
    await fillInstallFields()
    await submit("Connect to Slack")

    await waitFor(() => expect(goTo).toHaveBeenCalledWith(AUTHORIZE_URL))
    expect(onPage).toEqual([null])
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("refuses an authorization URL that is not https", async () => {
    serveBots([reservedSlackBot()])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(),
      authorization_url: "javascript:alert(1)",
    })
    renderPanel()
    await openDialog("Connect")
    await fillInstallFields()
    await submit("Connect to Slack")

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Could not connect the Slack bot",
          variant: "destructive",
        })
      )
    )
    expect(goTo).not.toHaveBeenCalled()
  })

  it("reconnects into the most recently disconnected bot", async () => {
    serveBots([
      reservedSlackBot({
        id: "bot-old",
        status: "disconnected",
        updated_at: "2026-11-10T00:00:00Z",
      }),
      reservedSlackBot({
        id: "bot-new",
        status: "disconnected",
        updated_at: "2026-11-11T00:00:00Z",
      }),
    ])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    await openDialog("Connect")
    expect(screen.getByLabelText("Endpoint URL")).toHaveValue(SLACK_ENDPOINT)
    await fillInstallFields()
    await submit("Connect to Slack")

    expect(api.createBot).not.toHaveBeenCalled()
    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({ bot_id: "bot-new" }),
    })
  })

  it("saves only the credentials entered", async () => {
    serveBots([slackBot()])
    api.updateBot.mockResolvedValue(slackBot())
    renderPanel()

    await openDialog("Manage")
    await userEvent.type(screen.getByLabelText("Signing secret"), "new")
    await submit("Save credentials")
    expect(api.updateBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      botId: "bot-1",
      requestBody: {
        credentials: { provider: "slack", signing_secret: "new" },
      },
    })
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("warns that reconnecting takes a working bot offline only when reconnecting", async () => {
    serveBots([slackBot()])
    renderPanel()
    await openDialog("Manage")

    expect(screen.queryByText(RECONNECT_WARNING)).toBeNull()
    await submit("Reconnect")
    expect(
      screen.getByRole("button", { name: "Reconnect to Slack" })
    ).toBeInTheDocument()
    expect(screen.getByText(RECONNECT_WARNING)).toBeInTheDocument()
  })

  it("disables every action while a bot mutation runs", async () => {
    serveBots([slackBot()])
    api.updateBot.mockReturnValue(
      new Promise(() => {}) as ReturnType<typeof api.updateBot>
    )
    renderPanel()
    await openDialog("Manage")

    await submit("Pause")
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled()
    )
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeDisabled()
    expect(screen.getByLabelText("Client ID")).toBeDisabled()
  })

  it("reconnects a bot that needs it into the same bot", async () => {
    serveBots([slackBot({ status: "needs_reconnect" })])
    api.installBot.mockResolvedValue({
      bot: slackBot(),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    await openDialog("Manage")
    expect(
      screen.queryByRole("button", { name: "Save credentials" })
    ).toBeNull()
    await userEvent.type(screen.getByLabelText("Client secret"), "cs")
    await userEvent.type(screen.getByLabelText("Signing secret"), "ss")
    await submit("Reconnect to Slack")

    expect(api.createBot).not.toHaveBeenCalled()
    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({ bot_id: "bot-1" }),
    })
  })

  it("keeps the form but drops sent secrets from the cache when saving fails", async () => {
    serveBots([slackBot()])
    api.updateBot.mockRejectedValue(apiError(400, { detail: "Bad secret" }))
    const { client } = renderPanel()
    await openDialog("Manage")
    await userEvent.type(screen.getByLabelText("Signing secret"), "s3cret")
    await submit("Save credentials")

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith({
        title: "Could not update the Slack bot",
        description: "Bad secret",
        variant: "destructive",
      })
    )
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByLabelText("Signing secret")).toHaveValue("s3cret")
    await waitFor(() =>
      expect(
        JSON.stringify(
          client
            .getMutationCache()
            .getAll()
            .map((mutation) => mutation.state.variables)
        )
      ).not.toContain("s3cret")
    )
  })

  it("stays open while reconnecting, so the redirect still happens", async () => {
    serveBots([slackBot()])
    let resolveInstall: (value: ChannelBotInstallResult) => void = () => {}
    api.installBot.mockReturnValue(
      new Promise<ChannelBotInstallResult>((resolve) => {
        resolveInstall = resolve
      }) as ReturnType<typeof api.installBot>
    )
    renderPanel()
    await openDialog("Manage")
    await submit("Reconnect")
    await userEvent.type(screen.getByLabelText("Client secret"), "cs")
    await userEvent.type(screen.getByLabelText("Signing secret"), "ss")
    await submit("Reconnect to Slack")

    await userEvent.keyboard("{Escape}")
    await submit("Close")
    expect(screen.getByRole("dialog")).toBeInTheDocument()

    resolveInstall({ bot: slackBot(), authorization_url: AUTHORIZE_URL })
    await waitFor(() => expect(goTo).toHaveBeenCalledWith(AUTHORIZE_URL))
  })

  it.each([
    "http://api.example.com/agent/channels/slack/abc",
    "https://localhost/agent/channels/slack/abc",
  ])("warns that Slack cannot reach %s", async (url) => {
    serveBots([slackBot({ endpoint_url: url })])
    renderPanel()
    await openDialog("Manage")
    expect(screen.getByLabelText("Endpoint URL")).toHaveAccessibleDescription(
      /Slack can't reach this address/
    )
  })

  it("does not warn about a public https endpoint", async () => {
    serveBots([slackBot()])
    renderPanel()
    await openDialog("Manage")
    expect(
      screen.getByLabelText("Endpoint URL")
    ).not.toHaveAccessibleDescription(/reach/)
  })

  it("asks for an update when only the manifest changed", async () => {
    serveBots([slackBot({}, { update_needed: true })])
    renderPanel()
    expect(await screen.findByText("Update needed")).toBeInTheDocument()
    expect(screen.queryByText(/to grant/)).toBeNull()
  })

  it("lists the scopes a reinstall must grant", async () => {
    serveBots([
      slackBot({}, { missing_scopes: ["users:read"], update_needed: true }),
    ])
    renderPanel()
    expect(await screen.findByText(/to grant: users:read/)).toBeInTheDocument()
  })
})

describe("SlackBotCard over Socket Mode", () => {
  // A Socket Mode bot has no endpoint URL, reserved or installed.
  const noEndpoint = { endpoint_url: null }
  const socket = { transport: "socket" as const }

  it("asks a reserved bot for the app-level token and shows no endpoint", async () => {
    serveBots([reservedSlackBot(noEndpoint, socket)])
    renderPanel()
    await openDialog("Connect")

    expect(screen.queryByLabelText("Endpoint URL")).toBeNull()
    expect(
      screen.getByText(/receives Slack events over Socket Mode/)
    ).toBeInTheDocument()
    await fillInstallFields()
    const connect = screen.getByRole("button", { name: "Connect to Slack" })
    expect(connect).toBeDisabled()
    await userEvent.type(screen.getByLabelText("App-level token"), "xapp-1")
    expect(connect).toBeEnabled()
  })

  it("installs with the app-level token in the exact body", async () => {
    serveBots([reservedSlackBot(noEndpoint, socket)])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(noEndpoint, socket),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    await openDialog("Connect")
    await fillInstallFields("xapp-1")
    await submit("Connect to Slack")

    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        provider: "slack",
        bot_id: "bot-1",
        agent_preset_id: "preset-1",
        client_id: "123.456",
        client_secret: "cs",
        signing_secret: "ss",
        app_token: "xapp-1",
        return_url: window.location.href,
      },
    })
  })

  it("asks an installed bot for the token only when reconnecting", async () => {
    serveBots([slackBot(noEndpoint, socket)])
    renderPanel()
    await openDialog("Manage")

    expect(screen.queryByLabelText("App-level token")).toBeNull()
    expect(screen.getByText(/replaces the app-level token/)).toBeInTheDocument()
    await submit("Reconnect")
    expect(screen.getByLabelText("App-level token")).toBeInTheDocument()
  })
})

describe("Reconnecting a bot that needs it", () => {
  // The backend reports the transport the next install will use.
  async function startReconnect(transport: "http" | "socket"): Promise<void> {
    serveBots([slackBot({ status: "needs_reconnect" }, { transport })])
    renderPanel()
    await openDialog("Manage")
    await fillInstallFields()
  }

  it("asks an HTTP bot for no app-level token", async () => {
    await startReconnect("http")
    expect(screen.queryByLabelText("App-level token")).toBeNull()
    expect(
      screen.getByRole("button", { name: "Reconnect to Slack" })
    ).toBeEnabled()
  })

  it("requires the app-level token of a Socket Mode bot", async () => {
    await startReconnect("socket")
    const reconnect = screen.getByRole("button", { name: "Reconnect to Slack" })
    expect(reconnect).toBeDisabled()
    await userEvent.type(screen.getByLabelText("App-level token"), "xapp-1")
    expect(reconnect).toBeEnabled()
  })
})
