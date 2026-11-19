import { MessagesSquare } from "lucide-react"
import type { ChannelBotProvider } from "@/client"
import { CHANNEL_BOT_PROVIDER_NAMES } from "@/components/agents/external-channels/channel-bot-utils"
import { SlackIcon } from "@/components/icons"
import { cn } from "@/lib/utils"

/** The provider's display name, or null for a provider Tracecat does not know. */
export function channelProviderName(provider: string): string | null {
  return Object.hasOwn(CHANNEL_BOT_PROVIDER_NAMES, provider)
    ? CHANNEL_BOT_PROVIDER_NAMES[provider as ChannelBotProvider]
    : null
}

/** The provider's logo, or a generic chat icon for one without a logo. */
export function ChannelProviderIcon({
  provider,
  className,
}: {
  provider: string
  className?: string
}) {
  if (provider === "slack") {
    return <SlackIcon className={className} />
  }
  return <MessagesSquare className={cn("text-muted-foreground", className)} />
}
