import {
  reservedSlackBot,
  slackBot,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import {
  channelBotReturnUrl,
  channelBotState,
  httpsUrlOrNull,
  isUnreachableEndpoint,
  pickChannelBot,
} from "@/components/agents/external-channels/channel-bot-utils"

jest.mock("@/client")

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
