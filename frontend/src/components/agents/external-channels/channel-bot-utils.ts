import type {
  AgentChannelBotRead,
  ChannelBotDetails,
  ChannelBotProvider,
} from "@/client"

/** Each provider's name as toasts and warnings show it. */
export const CHANNEL_BOT_PROVIDER_NAMES: Record<ChannelBotProvider, string> = {
  slack: "Slack",
}

/** What a provider card shows for its bot. */
export type ChannelBotState =
  | "not_connected"
  | "not_installed"
  | "active"
  | "paused"
  | "needs_reconnect"

/** Each state's badge, none before a bot is connected. */
export const CHANNEL_BOT_STATES: Record<
  ChannelBotState,
  {
    label: string | null
    /** Installed, so it can be managed and disconnected. */
    installed: boolean
  }
> = {
  not_connected: { label: null, installed: false },
  not_installed: { label: "Not installed", installed: false },
  active: { label: "Active", installed: true },
  paused: { label: "Paused", installed: true },
  needs_reconnect: { label: "Needs reconnect", installed: true },
}

/** The card state; `installed` is false until a provider's browser step ends. */
export function channelBotState(
  bot: AgentChannelBotRead | null,
  installed: boolean
): ChannelBotState {
  if (!bot || bot.status === "disconnected") {
    return "not_connected"
  }
  if (!installed) {
    return "not_installed"
  }
  return bot.status
}

/** One provider's member of the bot details union. */
export type ChannelBotDetailsOf<P extends ChannelBotProvider> = Extract<
  ChannelBotDetails,
  { provider: P }
>

function isDetailsOf<P extends ChannelBotProvider>(
  details: ChannelBotDetails,
  provider: P
): details is ChannelBotDetailsOf<P> {
  return details.provider === provider
}

/** A bot's details for `provider`, or null for no bot or another provider. */
export function channelBotDetails<P extends ChannelBotProvider>(
  bot: AgentChannelBotRead | null,
  provider: P
): ChannelBotDetailsOf<P> | null {
  if (!bot || !isDetailsOf(bot.details, provider)) {
    return null
  }
  return bot.details
}

/** Query parameters the OAuth callback adds to the return URL. */
export const CHANNEL_BOT_INSTALL_PARAM = "channel_bot_install"
export const CHANNEL_BOT_MESSAGE_PARAM = "channel_bot_message"

/** The current page, without a previous install's result parameters. */
export function channelBotReturnUrl(): string {
  const url = new URL(window.location.href)
  url.searchParams.delete(CHANNEL_BOT_INSTALL_PARAM)
  url.searchParams.delete(CHANNEL_BOT_MESSAGE_PARAM)
  return url.toString()
}

/** Sends the browser to a provider's consent screen. */
export function goToChannelBotAuthorization(url: string): void {
  window.location.assign(url)
}

function latestUpdated(
  bots: Array<AgentChannelBotRead>
): AgentChannelBotRead | null {
  let latest: AgentChannelBotRead | null = null
  for (const bot of bots) {
    if (!latest || Date.parse(bot.updated_at) > Date.parse(latest.updated_at)) {
      latest = bot
    }
  }
  return latest
}

/**
 * The bot a provider card manages: active, else installed, else the latest
 * disconnected, else reserved. Only an install sets `provider_app_id`.
 */
export function pickChannelBot(
  bots: Array<AgentChannelBotRead>
): AgentChannelBotRead | null {
  return (
    bots.find((bot) => bot.status === "active") ??
    bots.find(
      (bot) => bot.status !== "disconnected" && bot.provider_app_id !== null
    ) ??
    latestUpdated(bots.filter((bot) => bot.status === "disconnected")) ??
    latestUpdated(bots)
  )
}

/** `value` when it parses as an https URL, else null. */
export function httpsUrlOrNull(value: string | null): string | null {
  if (!value) {
    return null
  }
  try {
    return new URL(value).protocol === "https:" ? value : null
  } catch {
    return null
  }
}

const LOOPBACK_HOST = /^(localhost|.+\.localhost|127(\.\d{1,3}){3}|\[::1\])$/i

/** Whether a provider cannot call `url`: it is not https, or is a loopback host. */
export function isUnreachableEndpoint(url: string): boolean {
  try {
    const { protocol, hostname } = new URL(url)
    return protocol !== "https:" || LOOPBACK_HOST.test(hostname)
  } catch {
    return true
  }
}
