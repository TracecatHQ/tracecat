import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ChannelBotInstallResult } from "@/client"
import {
  api,
  apiError,
  channelPage,
  reservedSlackBot,
  SLACK_ENDPOINT,
  SLACK_REDIRECT,
  SLACK_SIGN_IN_REDIRECT,
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
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
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

// A first install asks for credentials once the app exists in Slack.
async function skipCreateStep(): Promise<void> {
  const skip = screen.queryByRole("button", { name: "I already have the app" })
  if (skip) {
    await userEvent.click(skip)
  }
}

async function fillInstallFields(appToken?: string): Promise<void> {
  await skipCreateStep()
  await userEvent.type(screen.getByLabelText("Client ID"), "123.456")
  await userEvent.type(screen.getByLabelText("Client secret"), "cs")
  await userEvent.type(screen.getByLabelText("Signing secret"), "ss")
  if (appToken) {
    await userEvent.type(screen.getByLabelText("Access token"), appToken)
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
    expect(await screen.findByLabelText("Request URL")).toHaveValue(
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
    await skipCreateStep()
    expect(screen.getByLabelText("Client secret")).toHaveAttribute(
      "autocomplete",
      "new-password"
    )
    expect(screen.queryByLabelText("Access token")).toBeNull()
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
    expect(screen.getByLabelText("Request URL")).toHaveValue(SLACK_ENDPOINT)
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
    expect(screen.getByLabelText("Request URL")).toHaveAccessibleDescription(
      /Slack can't reach this address/
    )
  })

  it("does not warn about a public https endpoint", async () => {
    serveBots([slackBot()])
    renderPanel()
    await openDialog("Manage")
    expect(
      screen.getByLabelText("Request URL")
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

// The manifest fields these tests read.
type SlackManifestFields = {
  display_information: { name: string }
  oauth_config: { redirect_urls: string[] }
  settings: {
    socket_mode_enabled: boolean
    token_rotation_enabled: boolean
    app_level_token_rotation_enabled?: boolean
    interactivity: { request_url?: string }
    event_subscriptions: { request_url?: string }
  }
}

describe("Creating the custom Slack app", () => {
  function manifestOf(link: HTMLElement): SlackManifestFields {
    const url = new URL(link.getAttribute("href") ?? "")
    expect(url.origin + url.pathname).toBe("https://api.slack.com/apps")
    expect(url.searchParams.get("new_app")).toBe("1")
    return JSON.parse(url.searchParams.get("manifest_json") ?? "")
  }

  it("names the bot, then opens Slack with its manifest filled in", async () => {
    serveBots([reservedSlackBot()])
    renderPanel()
    await openDialog("Connect")

    expect(screen.queryByLabelText("Client ID")).toBeNull()
    const name = screen.getByLabelText("Bot name")
    expect(name).toHaveValue("Triage")
    await userEvent.clear(name)
    await userEvent.type(name, "SOC bot")
    const link = screen.getByRole("link", { name: "Create in Slack" })
    const manifest = manifestOf(link)
    expect(manifest.display_information.name).toBe("SOC bot")
    expect(manifest.settings.event_subscriptions.request_url).toBe(
      SLACK_ENDPOINT
    )
    expect(manifest.settings.interactivity.request_url).toBe(SLACK_ENDPOINT)
    expect(manifest.settings.token_rotation_enabled).toBe(true)
    expect(manifest.oauth_config.redirect_urls).toEqual([
      SLACK_REDIRECT,
      SLACK_SIGN_IN_REDIRECT,
    ])

    await userEvent.click(link)
    expect(screen.getByText("Slack app ready")).toBeInTheDocument()
    expect(screen.getByLabelText("Client ID")).toHaveAccessibleDescription(
      "App Credentials → Client ID"
    )
  })

  it("fills in Socket Mode for a Socket Mode bot", async () => {
    serveBots([
      reservedSlackBot({ endpoint_url: null }, { transport: "socket" }),
    ])
    renderPanel()
    await openDialog("Connect")

    const manifest = manifestOf(
      screen.getByRole("link", { name: "Create in Slack" })
    )
    expect(manifest.settings.socket_mode_enabled).toBe(true)
    expect(manifest.settings.app_level_token_rotation_enabled).toBe(true)
    expect(manifest.settings.event_subscriptions.request_url).toBeUndefined()
  })

  it("keeps the request and redirect URLs in a collapsed manual section", async () => {
    serveBots([reservedSlackBot()])
    renderPanel()
    await openDialog("Connect")

    const summary = screen.getByText("Create the app by hand instead")
    expect(summary.closest("details")).not.toHaveAttribute("open")
    expect(screen.getByLabelText("Request URL")).not.toBeVisible()
    expect(screen.getByLabelText("OAuth redirect URL")).not.toBeVisible()
    await userEvent.click(summary)
    expect(screen.getByLabelText("Request URL")).toBeVisible()
  })

  it("says how to invite the bot once it is active", async () => {
    serveBots([slackBot()])
    renderPanel()
    expect(await screen.findByText(/with the bot's name/)).toBeInTheDocument()
    expect(
      screen.getByText("/invite", { selector: "code" })
    ).toBeInTheDocument()
    expect(screen.queryByText(/^In /)).not.toBeInTheDocument()
  })

  it("names the first channels an active bot is in", async () => {
    serveBots([slackBot()])
    api.listChannels.mockResolvedValue(
      channelPage(
        ["triage", "alerts", "soc"].map((name, i) => ({
          id: `C${i}`,
          name,
          is_private: name === "soc",
          is_member: true,
        }))
      )
    )
    renderPanel()
    expect(await screen.findByText("In #alerts, soc +1")).toBeInTheDocument()
    expect(api.listChannels).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      botId: "bot-1",
      limit: 100,
    })
  })

  it("marks the count a lower bound when the bot is in channels past the first page", async () => {
    serveBots([slackBot()])
    api.listChannels.mockResolvedValue(
      channelPage(
        ["triage", "alerts", "soc"].map((name, i) => ({
          id: `C${i}`,
          name,
          is_private: false,
          is_member: true,
        })),
        "next"
      )
    )
    renderPanel()
    expect(await screen.findByText("In #alerts, #soc +1+")).toBeInTheDocument()
  })

  it("lists no channels for a paused bot", async () => {
    serveBots([slackBot({ status: "paused" })])
    renderPanel()
    await screen.findByText("Custom Slack app")
    expect(api.listChannels).not.toHaveBeenCalled()
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

    expect(screen.queryByLabelText("Request URL")).toBeNull()
    expect(
      screen.getByText(/receives Slack events over Socket Mode/)
    ).toBeInTheDocument()
    await fillInstallFields()
    const connect = screen.getByRole("button", { name: "Connect to Slack" })
    expect(connect).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Access token"), "xapp-1")
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

  it("requires and sends the refresh token of a rotating app-level token", async () => {
    serveBots([reservedSlackBot(noEndpoint, socket)])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(noEndpoint, socket),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    await openDialog("Connect")
    await fillInstallFields("xoxe.xapp-1")
    const connect = screen.getByRole("button", { name: "Connect to Slack" })
    expect(connect).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Refresh token"), "xoxe-1")
    await submit("Connect to Slack")

    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({
        app_token: "xoxe.xapp-1",
        app_refresh_token: "xoxe-1",
      }),
    })
  })

  it("asks for both rotating tokens up front, in either order", async () => {
    serveBots([reservedSlackBot(noEndpoint, socket)])
    api.installBot.mockResolvedValue({
      bot: reservedSlackBot(noEndpoint, socket),
      authorization_url: AUTHORIZE_URL,
    })
    renderPanel()
    await openDialog("Connect")
    await fillInstallFields()
    await userEvent.type(screen.getByLabelText("Refresh token"), "xoxe-1")
    await userEvent.type(screen.getByLabelText("Access token"), "xoxe.xapp-1")
    await submit("Connect to Slack")

    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: expect.objectContaining({
        app_token: "xoxe.xapp-1",
        app_refresh_token: "xoxe-1",
      }),
    })
  })

  it("asks an installed bot for the token only when reconnecting", async () => {
    serveBots([slackBot(noEndpoint, socket)])
    renderPanel()
    await openDialog("Manage")

    expect(screen.queryByLabelText("Access token")).toBeNull()
    expect(screen.getByText(/replaces the app-level token/)).toBeInTheDocument()
    await submit("Reconnect")
    expect(screen.getByLabelText("Access token")).toBeInTheDocument()
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
    expect(screen.queryByLabelText("Access token")).toBeNull()
    expect(
      screen.getByRole("button", { name: "Reconnect to Slack" })
    ).toBeEnabled()
  })

  it("requires the app-level token of a Socket Mode bot", async () => {
    await startReconnect("socket")
    const reconnect = screen.getByRole("button", { name: "Reconnect to Slack" })
    expect(reconnect).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Access token"), "xapp-1")
    expect(reconnect).toBeEnabled()
  })
})
