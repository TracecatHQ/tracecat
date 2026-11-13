import { act, renderHook, waitFor } from "@testing-library/react"
import { type ReactNode, StrictMode } from "react"
import { organizationGetOrganizationEntitlements } from "@/client"
import {
  api,
  serveBots,
  serveEntitlements,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { toast } from "@/components/ui/use-toast"
import {
  useAgentChannelBots,
  useChannelBotInstallResult,
  useInstallAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client")
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

const INSTALL = {
  provider: "slack" as const,
  bot_id: null,
  agent_preset_id: "preset-1",
  client_id: "123.456",
  client_secret: "s3cret",
  signing_secret: "signing",
  return_url: "https://app.example.com/agents/a",
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { client, wrapper }
}

beforeEach(() => {
  jest.clearAllMocks()
  serveEntitlements()
  serveBots([slackBot()])
})

describe("useAgentChannelBots", () => {
  it("lists every provider's bots for the preset", async () => {
    const { wrapper } = setup()
    const { result } = renderHook(
      () => useAgentChannelBots("workspace-1", "preset-1"),
      { wrapper }
    )
    await waitFor(() => expect(result.current.bots).toHaveLength(1))
    expect(api.listBots).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      agentPresetId: "preset-1",
      limit: 100,
    })
  })

  it("never requests bots without the preview entitlement", async () => {
    serveEntitlements({ agent_channels: true })
    const { wrapper } = setup()
    const { result } = renderHook(
      () => useAgentChannelBots("workspace-1", "preset-1"),
      { wrapper }
    )
    await waitFor(() =>
      expect(organizationGetOrganizationEntitlements).toHaveBeenCalled()
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(api.listBots).not.toHaveBeenCalled()
    expect(result.current.bots).toBeUndefined()
  })
})

describe("useInstallAgentChannelBot", () => {
  it("refetches the bot list before the caller's onSuccess", async () => {
    api.installBot.mockResolvedValue({
      bot: slackBot(),
      authorization_url: null,
    })
    const { wrapper } = setup()
    const { result } = renderHook(
      () => ({
        list: useAgentChannelBots("workspace-1", "preset-1"),
        install: useInstallAgentChannelBot("workspace-1", "slack"),
      }),
      { wrapper }
    )
    await waitFor(() => expect(result.current.list.bots).toHaveLength(1))
    expect(api.listBots).toHaveBeenCalledTimes(1)

    const listCallsAtSuccess: number[] = []
    act(() => {
      result.current.install.installBot(INSTALL, {
        onSuccess: () =>
          listCallsAtSuccess.push(api.listBots.mock.calls.length),
      })
    })
    await waitFor(() => expect(listCallsAtSuccess).toEqual([2]))
    expect(api.installBot).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: INSTALL,
    })
  })

  it("runs the caller's onSuccess, then drops the secrets it sent", async () => {
    api.installBot.mockResolvedValue({
      bot: slackBot(),
      authorization_url: null,
    })
    const { client, wrapper } = setup()
    const onSuccess = jest.fn()
    const { result } = renderHook(
      () => useInstallAgentChannelBot("workspace-1", "slack"),
      { wrapper }
    )
    act(() => {
      result.current.installBot(INSTALL, { onSuccess })
    })
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
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
})

describe("useChannelBotInstallResult", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/")
  })

  it("reports a failed OAuth return and strips its parameters", () => {
    window.history.replaceState(
      {},
      "",
      "/agents/a?tab=channels&channel_bot_install=error&channel_bot_message=Denied"
    )
    renderHook(() => useChannelBotInstallResult())
    expect(toast).toHaveBeenCalledWith({
      title: "Could not connect the Slack bot",
      description: "Denied",
      variant: "destructive",
    })
    expect(window.location.search).toBe("?tab=channels")
  })

  it("ignores a message on a successful return", () => {
    window.history.replaceState(
      {},
      "",
      "/agents/a?channel_bot_install=success&channel_bot_message=Call+us"
    )
    renderHook(() => useChannelBotInstallResult())
    expect(toast).toHaveBeenCalledWith({ title: "Slack bot connected" })
    expect(window.location.search).toBe("")
  })

  it("trims a failure message and caps it at 200 characters", () => {
    const message = `  ${"x".repeat(300)}  `
    window.history.replaceState(
      {},
      "",
      `/agents/a?channel_bot_install=error&channel_bot_message=${encodeURIComponent(message)}`
    )
    renderHook(() => useChannelBotInstallResult())
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Could not connect the Slack bot",
        description: "x".repeat(200),
      })
    )
  })

  it("toasts once under StrictMode", () => {
    window.history.replaceState({}, "", "/agents/a?channel_bot_install=success")
    renderHook(() => useChannelBotInstallResult(), { wrapper: StrictMode })
    expect(toast).toHaveBeenCalledTimes(1)
  })
})
