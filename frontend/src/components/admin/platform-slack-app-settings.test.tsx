import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  adminSlackAppDeleteSlackApp,
  adminSlackAppGetSlackApp,
  adminSlackAppMarkSlackAppSetupSteps,
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
  mark: jest.mocked(adminSlackAppMarkSlackAppSetupSteps),
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
    icon_uploaded: false,
    distribution_enabled: false,
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

function renderSettings(): () => void {
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
  return render(<PlatformSlackAppSettings />, { wrapper: Wrapper }).unmount
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe("PlatformSlackAppSettings", () => {
  it("opens Slack with the platform manifest filled in", async () => {
    api.get.mockResolvedValue(slackApp())
    renderSettings()

    const link = await screen.findByRole("link", { name: "Create in Slack" })
    expect(link).toHaveAttribute("target", "_blank")
    const url = new URL(link.getAttribute("href") ?? "")
    expect(url.origin + url.pathname).toBe("https://api.slack.com/apps")
    expect(url.searchParams.get("new_app")).toBe("1")
    const manifest = JSON.parse(url.searchParams.get("manifest_json") ?? "")
    expect(manifest.settings.event_subscriptions.request_url).toBe(REQUEST_URL)
    expect(manifest.settings.interactivity.request_url).toBe(REQUEST_URL)
    expect(manifest.settings.token_rotation_enabled).toBe(true)
    expect(manifest.settings.socket_mode_enabled).toBe(false)
    expect(manifest.display_information.name).toBe("Tracecat")
    // Slack reads the manifest from the query string; keep it well under URL limits.
    expect(link.getAttribute("href")?.length).toBeLessThan(4000)
    expect(screen.queryByLabelText("App ID")).not.toBeInTheDocument()
  })

  it("fills in Socket Mode and app-level token rotation over Socket Mode", async () => {
    api.get.mockResolvedValue(
      slackApp({
        setup: { ...slackApp().setup, transport: "socket", request_url: null },
      })
    )
    renderSettings()

    const link = await screen.findByRole("link", { name: "Create in Slack" })
    const url = new URL(link.getAttribute("href") ?? "")
    const manifest = JSON.parse(url.searchParams.get("manifest_json") ?? "")
    expect(manifest.settings.socket_mode_enabled).toBe(true)
    expect(manifest.settings.app_level_token_rotation_enabled).toBe(true)
    expect(manifest.settings.token_rotation_enabled).toBe(true)
    expect(manifest.settings.event_subscriptions.request_url).toBeUndefined()
  })

  it("keeps the URLs and manifest in a collapsed manual section", async () => {
    api.get.mockResolvedValue(slackApp())
    renderSettings()

    const summary = await screen.findByText("Create the app by hand instead")
    const details = summary.closest("details")
    expect(details).not.toHaveAttribute("open")
    expect(screen.getByLabelText("Request URL")).not.toBeVisible()
    await userEvent.click(summary)
    expect(details).toHaveAttribute("open")
    expect(screen.getByLabelText("Request URL")).toHaveValue(REQUEST_URL)
    await userEvent.click(
      screen.getByRole("button", { name: "Copy app manifest" })
    )

    const copied = jest.mocked(copyToClipboard).mock.calls[0][0].value ?? ""
    const manifest = JSON.parse(copied)
    expect(manifest.oauth_config.redirect_urls).toEqual([
      "https://api.example.com/agent/channel-bots/slack/oauth/callback",
    ])
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining(["channels:read", "groups:read"])
    )
    expect(manifest.oauth_config.scopes.bot).not.toContain("channels:join")
  })

  it("registers a new app only once every credential is filled in", async () => {
    api.get.mockResolvedValue(slackApp())
    api.save.mockResolvedValue(SAVED)
    renderSettings()

    expect(await screen.findByText("Not registered")).toBeInTheDocument()
    await userEvent.click(
      await screen.findByRole("link", { name: "Create in Slack" })
    )
    // Opening Slack makes nothing yet, so no step claims the app is ready.
    expect(screen.queryByText("Slack app ready")).toBeNull()
    const save = screen.getByRole("button", { name: "Save" })
    expect(save).toBeDisabled()
    expect(screen.getByText("Fill in all four to save.")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("App ID"), "A0SYNTHETIC")
    await userEvent.type(screen.getByLabelText("Client ID"), "111.222")
    await userEvent.type(screen.getByLabelText("Client secret"), "client")
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Signing secret"), "signing")
    expect(screen.queryByText("Fill in all four to save.")).toBeNull()
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

  it("summarizes a registered app as ready, with what is left to do in Slack", async () => {
    api.get.mockResolvedValue(SAVED)
    renderSettings()

    const summary = await screen.findByRole("region", {
      name: "Tracecat for Slack",
    })
    expect(within(summary).getByText("Ready")).toBeInTheDocument()
    expect(within(summary).queryByText("Registered")).toBeNull()
    expect(within(summary).queryByText("Token rotation")).toBeNull()
    expect(within(summary).queryByText("Credentials saved")).toBeNull()
    expect(
      within(summary).getByRole("link", { name: "A0SYNTHETIC" })
    ).toHaveAttribute("href", "https://api.slack.com/apps/A0SYNTHETIC/general")
    expect(within(summary).getByText("HTTP")).toBeInTheDocument()

    const distribution = screen.getByRole("group", {
      name: "Turn on public distribution",
    })
    // Self-hosted installs in one Slack workspace; distribution is not required.
    expect(distribution).toHaveTextContent(
      "Until it's on, workspaces outside the one the app was created in can't add Tracecat."
    )
    expect(
      within(distribution).getByRole("link", {
        name: /Open Manage Distribution/,
      })
    ).toHaveAttribute(
      "href",
      "https://api.slack.com/apps/A0SYNTHETIC/distribute"
    )
    expect(screen.getByRole("link", { name: "Download logo" })).toHaveAttribute(
      "href",
      "/tracecat-icon-1024.png"
    )
    expect(screen.queryByLabelText("Client ID")).not.toBeInTheDocument()
  })

  it("drops each Slack-only step once marked done, and lists it with an undo", async () => {
    api.get.mockResolvedValue(SAVED)
    api.mark
      .mockResolvedValueOnce({ ...SAVED, icon_uploaded: true })
      .mockResolvedValueOnce({
        ...SAVED,
        icon_uploaded: true,
        distribution_enabled: true,
      })
    renderSettings()

    const icon = await screen.findByRole("group", {
      name: "Upload the Tracecat icon",
    })
    await userEvent.click(
      within(icon).getByRole("button", { name: "Mark done" })
    )
    expect(api.mark).toHaveBeenLastCalledWith({
      requestBody: { icon_uploaded: true },
    })
    expect(await screen.findByText("Marked uploaded")).toBeInTheDocument()
    expect(
      screen.queryByRole("group", { name: "Upload the Tracecat icon" })
    ).toBeNull()

    const distribution = screen.getByRole("group", {
      name: "Turn on public distribution",
    })
    await userEvent.click(
      within(distribution).getByRole("button", { name: "Mark done" })
    )
    expect(api.mark).toHaveBeenLastCalledWith({
      requestBody: { distribution_enabled: true },
    })
    expect(await screen.findByText("Marked on")).toBeInTheDocument()
    expect(
      screen.queryByRole("group", { name: "Turn on public distribution" })
    ).toBeNull()
    expect(screen.getByText("Ready")).toBeInTheDocument()
  })

  it("undoes a step marked done by mistake", async () => {
    api.get.mockResolvedValue({ ...SAVED, distribution_enabled: true })
    api.mark.mockResolvedValue(SAVED)
    renderSettings()

    await userEvent.click(
      await screen.findByRole("button", {
        name: "Undo marking distribution on",
      })
    )
    expect(api.mark).toHaveBeenCalledWith({
      requestBody: { distribution_enabled: false },
    })
    expect(
      await screen.findByRole("group", { name: "Turn on public distribution" })
    ).toBeInTheDocument()
    expect(screen.queryByText("Marked on")).toBeNull()
  })

  it("asks for the credentials again when the saved ones can't be read", async () => {
    api.get.mockResolvedValue(slackApp({ ...SAVED, unreadable: true }))
    renderSettings()

    expect(
      await screen.findByText("Can't read credentials")
    ).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "Paste the app's credentials again" })
    ).toBeInTheDocument()
    expect(screen.queryByText(/no longer decrypts/)).toBeNull()
    expect(
      screen.getByRole("button", { name: "Remove app" })
    ).toBeInTheDocument()
  })

  it("keeps saved secrets that are left blank", async () => {
    api.get.mockResolvedValue(SAVED)
    api.save.mockResolvedValue(SAVED)
    renderSettings()

    await userEvent.click(
      await screen.findByRole("button", { name: "Replace credentials" })
    )
    const clientId = screen.getByLabelText("Client ID")
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

    await userEvent.click(
      await screen.findByRole("button", { name: "Replace credentials" })
    )
    const save = screen.getByRole("button", { name: "Save" })
    expect(screen.queryByLabelText("Request URL")).not.toBeInTheDocument()
    expect(screen.getByText(/Generate Token and Scopes/)).toBeInTheDocument()
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Access token"), "xapp-1")
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

    await userEvent.click(
      await screen.findByRole("button", { name: "Replace credentials" })
    )
    const save = screen.getByRole("button", { name: "Save" })
    expect(screen.getByLabelText("Refresh token")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Access token"), "xoxe.xapp-1")
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Refresh token"), "xoxe-1")
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

    await userEvent.click(
      await screen.findByRole("button", { name: "Remove app" })
    )
    const dialog = await screen.findByRole("alertdialog")
    expect(api.remove).not.toHaveBeenCalled()
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Remove" })
    )

    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1))
  })

  it("shows why removal was refused while organizations have it installed", async () => {
    const detail =
      "1 Slack workspace(s) still have the Tracecat Slack app installed; disconnect them before removing the app"
    api.get.mockResolvedValue(SAVED)
    api.remove.mockRejectedValue(
      Object.assign(new Error("Conflict"), { status: 409, body: { detail } })
    )
    renderSettings()

    await userEvent.click(
      await screen.findByRole("button", { name: "Remove app" })
    )
    const dialog = await screen.findByRole("alertdialog")
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Remove" })
    )

    expect(await screen.findByText(detail)).toBeInTheDocument()
  })
})
