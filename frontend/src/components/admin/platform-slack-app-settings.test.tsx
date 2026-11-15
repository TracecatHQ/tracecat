import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  adminSlackAppDeleteSlackApp,
  adminSlackAppGetSlackApp,
  adminSlackAppSaveSlackApp,
  type SlackPlatformAppRead,
} from "@/client"
import { PlatformSlackAppSettings } from "@/components/admin/platform-slack-app-settings"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { copyToClipboard } from "@/lib/utils"

jest.mock("@/client")
jest.mock("@/lib/utils", () => ({
  ...jest.requireActual("@/lib/utils"),
  copyToClipboard: jest.fn(),
}))

const api = {
  get: jest.mocked(adminSlackAppGetSlackApp),
  save: jest.mocked(adminSlackAppSaveSlackApp),
  remove: jest.mocked(adminSlackAppDeleteSlackApp),
}
const REQUEST_URL = "https://api.example.com/platform-events"

function slackApp(
  overrides: Partial<SlackPlatformAppRead> = {}
): SlackPlatformAppRead {
  return {
    app_id: null,
    client_id: null,
    client_secret_set: false,
    signing_secret_set: false,
    app_token_set: false,
    unreadable: false,
    setup: {
      transport: "http",
      oauth_redirect_url:
        "https://api.example.com/agent/channel-bots/slack/oauth/callback",
      request_url: REQUEST_URL,
    },
    ...overrides,
  }
}

const SAVED = slackApp({
  app_id: "A0SYNTHETIC",
  client_id: "111.222",
  client_secret_set: true,
  signing_secret_set: true,
})

function renderSettings(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    )
  }
  render(<PlatformSlackAppSettings />, { wrapper: Wrapper })
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe("PlatformSlackAppSettings", () => {
  it("shows the URLs to paste into Slack and copies the manifest", async () => {
    api.get.mockResolvedValue(slackApp())
    renderSettings()

    expect(await screen.findByLabelText("Request URL")).toHaveValue(REQUEST_URL)
    expect(screen.queryByLabelText("App-level token")).not.toBeInTheDocument()
    await userEvent.click(
      screen.getByRole("button", { name: "Copy app manifest" })
    )

    const copied = jest.mocked(copyToClipboard).mock.calls[0][0].value ?? ""
    const manifest = JSON.parse(copied)
    expect(manifest.settings.event_subscriptions.request_url).toBe(REQUEST_URL)
    expect(manifest.oauth_config.redirect_urls).toEqual([
      "https://api.example.com/agent/channel-bots/slack/oauth/callback",
    ])
    expect(manifest.settings.token_rotation_enabled).toBe(true)
  })

  it("registers a new app only once every credential is filled in", async () => {
    api.get.mockResolvedValue(slackApp())
    api.save.mockResolvedValue(SAVED)
    renderSettings()

    const save = await screen.findByRole("button", { name: "Save" })
    await userEvent.type(screen.getByLabelText("App ID"), "A0SYNTHETIC")
    await userEvent.type(screen.getByLabelText("Client ID"), "111.222")
    await userEvent.type(screen.getByLabelText("Client secret"), "client")
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Signing secret"), "signing")
    await userEvent.click(save)

    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith({
        requestBody: {
          app_id: "A0SYNTHETIC",
          client_id: "111.222",
          client_secret: "client",
          signing_secret: "signing",
          app_token: null,
          app_refresh_token: null,
        },
      })
    )
  })

  it("keeps saved secrets that are left blank", async () => {
    api.get.mockResolvedValue(SAVED)
    api.save.mockResolvedValue(SAVED)
    renderSettings()

    const clientId = await screen.findByLabelText("Client ID")
    expect(screen.getByLabelText("Client secret")).toHaveAttribute(
      "placeholder",
      "Saved; leave blank to keep"
    )
    await userEvent.clear(clientId)
    await userEvent.type(clientId, "333.444")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith({
        requestBody: {
          app_id: "A0SYNTHETIC",
          client_id: "333.444",
          client_secret: null,
          signing_secret: null,
          app_token: null,
          app_refresh_token: null,
        },
      })
    )
  })

  it("needs the app-level token over Socket Mode", async () => {
    api.get.mockResolvedValue(
      slackApp({
        ...SAVED,
        setup: { ...SAVED.setup, transport: "socket", request_url: null },
      })
    )
    renderSettings()

    const save = await screen.findByRole("button", { name: "Save" })
    expect(screen.queryByLabelText("Request URL")).not.toBeInTheDocument()
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("App-level token"), "xapp-1")
    expect(save).toBeEnabled()
  })

  it("saves a rotating app-level token with its refresh token", async () => {
    const socket = slackApp({
      ...SAVED,
      setup: { ...SAVED.setup, transport: "socket", request_url: null },
    })
    api.get.mockResolvedValue(socket)
    api.save.mockResolvedValue(socket)
    renderSettings()

    const save = await screen.findByRole("button", { name: "Save" })
    expect(
      screen.queryByLabelText("App-level refresh token")
    ).not.toBeInTheDocument()
    await userEvent.type(
      screen.getByLabelText("App-level token"),
      "xoxe.xapp-1"
    )
    expect(save).toBeDisabled()
    await userEvent.type(
      screen.getByLabelText("App-level refresh token"),
      "xoxe-1"
    )
    await userEvent.click(save)

    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith({
        requestBody: {
          app_id: "A0SYNTHETIC",
          client_id: "111.222",
          client_secret: null,
          signing_secret: null,
          app_token: "xoxe.xapp-1",
          app_refresh_token: "xoxe-1",
        },
      })
    )
  })

  it("removes the app after a confirmation", async () => {
    api.get.mockResolvedValue(SAVED)
    api.remove.mockResolvedValue(undefined)
    renderSettings()

    await userEvent.click(await screen.findByRole("button", { name: "Remove" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(api.remove).not.toHaveBeenCalled()
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Remove" })
    )

    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1))
  })
})
