import type { ChannelBotChannel, ChannelBotProvider } from "@/client"
import {
  reservedSlackBot,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import {
  channelBotReturnUrl,
  channelBotState,
  channelConventions,
  channelLabel,
  copyOrDownloadAppManifest,
  httpsUrlOrNull,
  isUnreachableEndpoint,
  pickChannelBot,
} from "@/components/agents/external-channels/channel-bot-utils"
import { copyToClipboard } from "@/lib/utils"

jest.mock("@/client")
jest.mock("@/lib/utils", () => ({
  ...jest.requireActual("@/lib/utils"),
  copyToClipboard: jest.fn(),
}))

describe("channelBotState", () => {
  it("is not connected without a live bot", () => {
    expect(channelBotState(null, true)).toBe("not_connected")
    expect(channelBotState(slackBot({ status: "disconnected" }), true)).toBe(
      "not_connected"
    )
  })

  it("is not installed until the browser step ends, else the bot status", () => {
    expect(channelBotState(slackBot(), false)).toBe("not_installed")
    expect(channelBotState(slackBot({ status: "paused" }), true)).toBe("paused")
    expect(channelBotState(slackBot({ status: "needs_reconnect" }), true)).toBe(
      "needs_reconnect"
    )
  })
})

describe("pickChannelBot", () => {
  const reserved = reservedSlackBot({
    id: "reserved",
    updated_at: "2026-11-12T00:00:00Z",
  })
  const older = reservedSlackBot({
    id: "older",
    status: "disconnected",
    updated_at: "2026-11-10T00:00:00Z",
  })
  const newer = reservedSlackBot({
    id: "newer",
    status: "disconnected",
    updated_at: "2026-11-11T00:00:00Z",
  })
  const paused = slackBot({ id: "paused", status: "paused" })
  const active = slackBot({ id: "active" })

  it("prefers the active bot, then an installed live one", () => {
    expect(pickChannelBot([reserved, newer, paused, active])?.id).toBe("active")
    expect(pickChannelBot([reserved, newer, paused])?.id).toBe("paused")
  })

  it("prefers a disconnected bot that was installed over a reserved one", () => {
    expect(pickChannelBot([reserved, older, newer])?.id).toBe("newer")
    expect(pickChannelBot([newer, older, reserved])?.id).toBe("newer")
  })

  it("falls back to a reserved bot, then to none", () => {
    expect(pickChannelBot([reserved])?.id).toBe("reserved")
    expect(pickChannelBot([])).toBeNull()
  })
})

describe("isUnreachableEndpoint", () => {
  it("flags addresses a provider cannot call", () => {
    expect(isUnreachableEndpoint("https://api.example.com/x")).toBe(false)
    expect(isUnreachableEndpoint("http://api.example.com/x")).toBe(true)
    expect(isUnreachableEndpoint("https://localhost/x")).toBe(true)
    expect(isUnreachableEndpoint("https://api.localhost/x")).toBe(true)
    expect(isUnreachableEndpoint("https://127.0.0.1:8000/x")).toBe(true)
    expect(isUnreachableEndpoint("https://[::1]/x")).toBe(true)
    expect(isUnreachableEndpoint("not a url")).toBe(true)
    for (const host of [
      "0.0.0.0",
      "10.1.2.3",
      "172.16.0.1",
      "172.31.255.255",
      "192.168.1.10",
      "169.254.169.254",
    ]) {
      expect(isUnreachableEndpoint(`https://${host}/x`)).toBe(true)
    }
    expect(isUnreachableEndpoint("https://172.32.0.1/x")).toBe(false)
    expect(isUnreachableEndpoint("https://11.0.0.1/x")).toBe(false)
  })
})

describe("httpsUrlOrNull", () => {
  it("keeps only https URLs", () => {
    expect(httpsUrlOrNull("https://slack.com/oauth/v2/authorize?x=1")).toBe(
      "https://slack.com/oauth/v2/authorize?x=1"
    )
    expect(httpsUrlOrNull("http://slack.com/oauth")).toBeNull()
    expect(httpsUrlOrNull("javascript:alert(1)")).toBeNull()
    expect(httpsUrlOrNull("/relative")).toBeNull()
    expect(httpsUrlOrNull(null)).toBeNull()
  })
})

describe("channelBotReturnUrl", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/")
  })

  it("drops a previous install's result parameters", () => {
    window.history.replaceState(
      {},
      "",
      "/agents/a?tab=channels&channel_bot_install=error&channel_bot_message=Denied"
    )
    expect(channelBotReturnUrl()).toBe("http://localhost/agents/a?tab=channels")
  })
})

describe("channelConventions", () => {
  // No other provider is in the client yet; any non-Slack one reads as opaque.
  const teams = "teams" as ChannelBotProvider

  it("uppercases a Slack ID and accepts only Slack channel IDs", () => {
    const slack = channelConventions("slack")
    expect(slack.normalizeId("  c0123abcd ")).toBe("C0123ABCD")
    expect(slack.isChannelId("C0123ABCD")).toBe(true)
    expect(slack.isChannelId("D0123ABCD")).toBe(false)
    expect(slack.isChannelId(slack.normalizeId("general"))).toBe(false)
  })

  it("only trims another provider's case-sensitive ID", () => {
    const opaque = channelConventions(teams)
    const id = "19:LpxShHZZh9utjNcEmUS5aOEP9ASw85OUn05NcWYAhX81@thread.tacv2"
    expect(opaque.normalizeId(` ${id} `)).toBe(id)
    expect(opaque.isChannelId(id)).toBe(true)
    expect(opaque.isChannelId("a b")).toBe(false)
    expect(opaque.inviteCommand).toBeNull()
  })
})

describe("channelLabel", () => {
  const channel: ChannelBotChannel = {
    id: "C1",
    name: "alerts",
    is_private: false,
    is_member: true,
  }

  it("prefixes a public Slack channel with #, and not a private one", () => {
    expect(channelLabel("slack", channel)).toBe("#alerts")
    expect(channelLabel("slack", { ...channel, is_private: true })).toBe(
      "alerts"
    )
  })

  it("puts the team first when the channel names one", () => {
    expect(channelLabel("slack", { ...channel, team_name: "SecOps" })).toBe(
      "SecOps › #alerts"
    )
    expect(
      channelLabel("teams" as ChannelBotProvider, {
        ...channel,
        name: "General",
        team_name: "SecOps",
      })
    ).toBe("SecOps › General")
  })
})

describe("copyOrDownloadAppManifest", () => {
  const createObjectURL = jest.fn((_blob: Blob) => "blob:app-package")
  const revokeObjectURL = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
    Object.assign(URL, { createObjectURL, revokeObjectURL })
  })

  it("copies a text manifest as served", () => {
    copyOrDownloadAppManifest({
      filename: "slack-app-manifest.json",
      media_type: "application/json",
      encoding: "text",
      content: '{\n  "a": 1\n}',
      create_url: null,
    })

    expect(copyToClipboard).toHaveBeenCalledWith(
      expect.objectContaining({ value: '{\n  "a": 1\n}' })
    )
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it("downloads a binary manifest as its file, decoded from base64", async () => {
    const click = jest
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        expect(this.download).toBe("app-package.zip")
        expect(this.href).toBe("blob:app-package")
      })
    jest.useFakeTimers()

    copyOrDownloadAppManifest({
      filename: "app-package.zip",
      media_type: "application/zip",
      encoding: "base64",
      content: btoa("PK\u0005\u0006"),
      create_url: null,
    })

    expect(copyToClipboard).not.toHaveBeenCalled()
    expect(click).toHaveBeenCalledTimes(1)
    // Revoked after the click returns, so the browser can start the download.
    expect(revokeObjectURL).not.toHaveBeenCalled()
    jest.runAllTimers()
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:app-package")
    jest.useRealTimers()
    const blob = createObjectURL.mock.calls[0][0]
    expect(blob.type).toBe("application/zip")
    // jsdom's Blob has no arrayBuffer().
    const bytes = await new Promise<ArrayBuffer>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as ArrayBuffer)
      reader.readAsArrayBuffer(blob)
    })
    expect(new Uint8Array(bytes)).toEqual(
      new Uint8Array([0x50, 0x4b, 0x05, 0x06])
    )
    click.mockRestore()
  })
})
