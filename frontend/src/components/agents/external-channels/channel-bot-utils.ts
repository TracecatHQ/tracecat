import type {
  AgentChannelBotRead,
  ChannelBotAppManifest,
  ChannelBotChannel,
  ChannelBotDetails,
  ChannelBotProvider,
} from "@/client"
import { downloadBlob } from "@/lib/download"
import { getErrorStatus } from "@/lib/errors"
import { copyToClipboard } from "@/lib/utils"

/** Each provider's name as toasts and warnings show it. */
export const CHANNEL_BOT_PROVIDER_NAMES: Record<ChannelBotProvider, string> = {
  slack: "Slack",
}

/** How a provider writes channel IDs and names, and how its bot gets into a channel. */
export type ChannelConventions = {
  /** The ID events carry for what an admin typed or pasted. */
  normalizeId: (raw: string) => string
  /** Whether a normalized ID can be one of the provider's channel IDs. */
  isChannelId: (id: string) => boolean
  idPlaceholder: string
  /** Shown before a public channel's name. */
  publicPrefix: string
  /** The command a member runs to bring the bot into a channel; null without one. */
  inviteCommand: string | null
}

const SLACK_CHANNELS: ChannelConventions = {
  // Events carry uppercase IDs and never match a lowercase one.
  normalizeId: (raw) => raw.trim().toUpperCase(),
  // A digit keeps words such as GENERAL from passing as IDs.
  isChannelId: (id) => /^[CG](?=[A-Z0-9]*\d)[A-Z0-9]{6,}$/.test(id),
  idPlaceholder: "C0123456789",
  publicPrefix: "#",
  inviteCommand: "/invite @Tracecat",
}

// Opaque, case-sensitive IDs such as Teams' `19:…@thread.tacv2`.
const OPAQUE_CHANNELS: ChannelConventions = {
  normalizeId: (raw) => raw.trim(),
  isChannelId: (id) => /^\S+$/.test(id),
  idPlaceholder: "Channel ID",
  publicPrefix: "",
  inviteCommand: null,
}

export function channelConventions(
  provider: ChannelBotProvider
): ChannelConventions {
  return provider === "slack" ? SLACK_CHANNELS : OPAQUE_CHANNELS
}

/** A channel's name beside its icon, after its team when the tenant spans several. */
export function channelName(channel: ChannelBotChannel): string {
  return channel.team_name
    ? `${channel.team_name} › ${channel.name}`
    : channel.name
}

/** Orders channels by their name, after their team when the tenant spans several. */
export function byChannelName(
  a: ChannelBotChannel,
  b: ChannelBotChannel
): number {
  return channelName(a).localeCompare(channelName(b))
}

/** Whether the API refused to list channels because the provider cannot. */
export function isChannelListingNotSupported(error: unknown): boolean {
  return getErrorStatus(error) === 501
}

/** Whether the API refused to list channels until the bot's app is reinstalled. */
export function isChannelListingNeedsReinstall(error: unknown): boolean {
  return getErrorStatus(error) === 424
}

/** A channel in words, without an icon: `Team › #name` on Slack. */
export function channelLabel(
  provider: ChannelBotProvider,
  channel: ChannelBotChannel
): string {
  const prefix = channel.is_private
    ? ""
    : channelConventions(provider).publicPrefix
  return channelName({ ...channel, name: `${prefix}${channel.name}` })
}

/** The platform Slack app's name wherever a screen shows it. */
export const PLATFORM_SLACK_TITLE = "Tracecat for Slack"

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

/**
 * Whether an installed Slack bot lacks scopes the current manifest needs. A bot
 * that needs reconnecting is left out: its reconnect requests every scope.
 */
export function slackUpdateNeeded(
  details: ChannelBotDetailsOf<"slack"> | null,
  state: ChannelBotState
): boolean {
  return (
    CHANNEL_BOT_STATES[state].installed &&
    state !== "needs_reconnect" &&
    (details?.update_needed === true ||
      (details?.missing_scopes.length ?? 0) > 0)
  )
}

/** A routed channel's label: its name when listed, else its ID. */
export function routeChannelLabel(
  provider: ChannelBotProvider,
  providerChannelId: string,
  known: ChannelBotChannel | undefined
): string {
  return known ? channelLabel(provider, known) : providerChannelId
}

/** The first `cap` channels, and ` +N` (`+N+` when more are unloaded) for the rest. */
export function channelSummary<T>(
  channels: T[],
  cap: number,
  hasMore: boolean
): { shown: T[]; overflow: string | null } {
  const hidden = channels.length - cap
  return {
    shown: channels.slice(0, cap),
    overflow: hidden > 0 ? ` +${hidden}${hasMore ? "+" : ""}` : null,
  }
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
/** A stable code naming why an install failed, beside the readable message. */
export const CHANNEL_BOT_ERROR_PARAM = "channel_bot_error"

/** The current page, without a previous install's result parameters. */
export function channelBotReturnUrl(): string {
  const url = new URL(window.location.href)
  url.searchParams.delete(CHANNEL_BOT_INSTALL_PARAM)
  url.searchParams.delete(CHANNEL_BOT_MESSAGE_PARAM)
  url.searchParams.delete(CHANNEL_BOT_ERROR_PARAM)
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

/** The workspace's platform install and its state; only a finished install records the app. */
export function platformInstallState(
  installs: Array<AgentChannelBotRead> | undefined
): { install: AgentChannelBotRead | null; state: ChannelBotState } {
  const install = installs ? pickChannelBot(installs) : null
  return {
    install,
    state: channelBotState(install, install?.provider_app_id != null),
  }
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

// Loopback, unspecified, link-local, and RFC 1918 private hosts.
const PRIVATE_HOST =
  /^(localhost|.+\.localhost|0\.0\.0\.0|127(\.\d{1,3}){3}|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|169\.254(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}|\[::1\])$/i

/** Whether a provider cannot call `url`: it is not https, or its host is private. */
export function isUnreachableEndpoint(url: string): boolean {
  try {
    const { protocol, hostname } = new URL(url)
    return protocol !== "https:" || PRIVATE_HOST.test(hostname)
  } catch {
    return true
  }
}

/** Copies a text app manifest, or downloads a binary one as its file. */
export function copyOrDownloadAppManifest(
  manifest: ChannelBotAppManifest
): void {
  if (manifest.encoding === "text") {
    void copyToClipboard({
      value: manifest.content,
      message: "App manifest copied",
    })
    return
  }
  const bytes = Uint8Array.from(atob(manifest.content), (char) =>
    char.charCodeAt(0)
  )
  downloadBlob(
    new Blob([bytes], { type: manifest.media_type }),
    manifest.filename
  )
}
