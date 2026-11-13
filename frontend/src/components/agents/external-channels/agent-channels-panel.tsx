"use client"

import type {
  AgentChannelBotRead,
  AgentPresetRead,
  ChannelBotProvider,
} from "@/client"
import { pickChannelBot } from "@/components/agents/external-channels/channel-bot-utils"
import { SlackBotCard } from "@/components/agents/external-channels/slack-bot-card"
import { SlackChannelPanel } from "@/components/agents/external-channels/slack-channel-panel"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  useAgentChannelBots,
  useChannelBotInstallResult,
} from "@/hooks/use-agent-channel-bots"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail, isEntitlementRequiredError } from "@/lib/errors"

/**
 * The Channels tab: the channel bot API for organizations with the preview
 * entitlement, legacy channel tokens otherwise. Mounted only once
 * entitlements have loaded.
 */
export function AgentChannelsPanel({
  workspaceId,
  preset,
}: {
  workspaceId: string
  preset: AgentPresetRead | null
}) {
  const { hasEntitlement } = useEntitlements()
  if (!preset) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-muted-foreground">
        Save this agent preset before configuring external channels.
      </div>
    )
  }
  if (!hasEntitlement("agent_channels_preview")) {
    return <SlackChannelPanel workspaceId={workspaceId} preset={preset} />
  }
  return <ChannelBotsPanel workspaceId={workspaceId} preset={preset} />
}

/** One card per provider, on the channel bot API. */
function ChannelBotsPanel({
  workspaceId,
  preset,
}: {
  workspaceId: string
  preset: AgentPresetRead
}) {
  useChannelBotInstallResult()
  const { bots, botsIsLoading, botsError } = useAgentChannelBots(
    workspaceId,
    preset.id
  )

  if (botsIsLoading) {
    return <CenteredSpinner />
  }
  // A failed refresh keeps the loaded bots, and with them any open dialog.
  if (botsError && !bots) {
    // Stale cached entitlements, or a deployment without the enterprise extension.
    if (isEntitlementRequiredError(botsError)) {
      return (
        <EntitlementRequiredEmptyState
          title="Channels unavailable"
          description={
            botsError.body?.message ??
            "Agent channels are not available on this deployment."
          }
        />
      )
    }
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertTitle>Unable to load channel settings</AlertTitle>
          <AlertDescription>
            {getApiErrorDetail(botsError) ?? botsError.message}
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  function botFor(provider: ChannelBotProvider): AgentChannelBotRead | null {
    return pickChannelBot(
      (bots ?? []).filter((bot) => bot.provider === provider)
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 px-4 pt-4 pb-16">
        <SlackBotCard
          workspaceId={workspaceId}
          preset={preset}
          bot={botFor("slack")}
        />
      </div>
    </ScrollArea>
  )
}
