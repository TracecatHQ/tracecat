"use client"

import Link from "next/link"
import type { ReactNode } from "react"
import type {
  AgentChannelBotRead,
  AgentPresetRead,
  ChannelBotProvider,
} from "@/client"
import { useAgentsChannelsHref } from "@/components/agents/external-channels/agents-channels-href"
import {
  ChannelBotHeader,
  ChannelBotStatus,
} from "@/components/agents/external-channels/channel-bot-card"
import {
  CHANNEL_BOT_STATES,
  type ChannelBotState,
  channelBotDetails,
  channelSummary,
  PLATFORM_SLACK_TITLE,
  pickChannelBot,
  platformInstallState,
  routeChannelLabel,
  slackUpdateNeeded,
} from "@/components/agents/external-channels/channel-bot-utils"
import { SlackBotCard } from "@/components/agents/external-channels/slack-bot-card"
import { SlackChannelPanel } from "@/components/agents/external-channels/slack-channel-panel"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  useAgentChannelBots,
  useChannelBotInstallResult,
  useChannelBotRoutableChannels,
  useChannelRoutesToAgent,
  usePlatformChannelBots,
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

/** Tracecat for Slack as it answers for this agent, then the agent's own Slack app. */
function ChannelBotsPanel({
  workspaceId,
  preset,
}: {
  workspaceId: string
  preset: AgentPresetRead
}) {
  // Toasts a custom app's OAuth return.
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
      <div className="flex flex-col gap-3 px-4 pt-4 pb-16">
        <PlatformSlackCard
          workspaceId={workspaceId}
          agentPresetId={preset.id}
        />
        <div className="rounded-md border">
          <SlackBotCard
            workspaceId={workspaceId}
            preset={preset}
            bot={botFor("slack")}
          />
        </div>
      </div>
    </ScrollArea>
  )
}

const CHANNEL_CHIP_CAP = 3

/** Tracecat for Slack and where it answers with this agent, once the deployment registers it. */
function PlatformSlackCard({
  workspaceId,
  agentPresetId,
}: {
  workspaceId: string
  agentPresetId: string
}) {
  const channelsHref = useAgentsChannelsHref(workspaceId)
  const { installs } = usePlatformChannelBots(
    workspaceId,
    "slack",
    channelsHref !== undefined
  )
  const canUpdateAgent = useScopeCheck("agent:update") === true
  // A skipped query still returns data cached before the gate closed.
  if (channelsHref === undefined || !installs) {
    return null
  }
  const { install, state } = platformInstallState(installs)
  if (!install || !CHANNEL_BOT_STATES[state].installed) {
    return (
      <PlatformSlackRow
        status={null}
        description="Not added to this workspace's Slack yet."
        action={
          canUpdateAgent ? (
            <ChannelsLink href={channelsHref}>Add to Slack</ChannelsLink>
          ) : null
        }
      />
    )
  }
  return (
    <PlatformSlackPlacement
      workspaceId={workspaceId}
      install={install}
      state={state}
      agentPresetId={agentPresetId}
      channelsHref={channelsHref}
      canUpdateAgent={canUpdateAgent}
    />
  )
}

function PlatformSlackPlacement({
  workspaceId,
  install,
  state,
  agentPresetId,
  channelsHref,
  canUpdateAgent,
}: {
  workspaceId: string
  install: AgentChannelBotRead
  state: ChannelBotState
  agentPresetId: string
  channelsHref: string
  canUpdateAgent: boolean
}) {
  // The default agent answers everywhere, so its row needs no channel lookups.
  const isDefault = install.agent_preset_id === agentPresetId
  const { routesToAgent, hasMoreRoutesToAgent } = useChannelRoutesToAgent(
    workspaceId,
    install.id,
    agentPresetId,
    { enabled: !isDefault }
  )
  // The Channels page's list, so routed channels the bot hasn't joined get names
  // too. Only a running install can list them; others show channel IDs.
  const { channels } = useChannelBotRoutableChannels(workspaceId, install.id, {
    enabled: !isDefault && state === "active",
  })
  const updateNeeded = slackUpdateNeeded(
    channelBotDetails(install, "slack"),
    state
  )
  // Active is the usual case, so only a state that needs attention shows.
  const status =
    state === "active" && !updateNeeded ? null : (
      <ChannelBotStatus state={state} updateNeeded={updateNeeded} />
    )
  const editChannels = (
    <ChannelsLink href={channelsHref}>
      {canUpdateAgent ? "Edit channels" : "View channels"}
    </ChannelsLink>
  )

  if (isDefault) {
    return (
      <PlatformSlackRow
        status={status}
        description="Default agent. Answers DMs and all other channels."
        action={editChannels}
      />
    )
  }
  // Loading or failed: say nothing rather than guess.
  if (!routesToAgent) {
    return null
  }
  if (routesToAgent.length === 0) {
    return (
      <PlatformSlackRow
        status={status}
        description="Not answering in any channel yet."
        action={
          canUpdateAgent ? (
            <ChannelsLink href={channelsHref}>Assign a channel</ChannelsLink>
          ) : null
        }
      />
    )
  }
  const channelsById = new Map((channels ?? []).map((c) => [c.id, c]))
  const chips = routesToAgent.map((route) => ({
    id: route.provider_channel_id,
    label: routeChannelLabel(
      "slack",
      route.provider_channel_id,
      channelsById.get(route.provider_channel_id)
    ),
  }))
  const summary = channelSummary(chips, CHANNEL_CHIP_CAP, hasMoreRoutesToAgent)
  return (
    <PlatformSlackRow
      status={status}
      description={
        <span className="mt-1 flex flex-wrap items-center gap-1">
          Answers in
          {summary.shown.map((chip) => (
            <Badge
              key={chip.id}
              variant="secondary"
              className="h-5 px-2 text-[10px] font-normal"
            >
              {chip.label}
            </Badge>
          ))}
          {summary.overflow}
        </span>
      }
      action={editChannels}
    />
  )
}

function PlatformSlackRow({
  status,
  description,
  action,
}: {
  status: ReactNode
  description: ReactNode
  action: ReactNode
}) {
  return (
    <div
      role="group"
      aria-label={PLATFORM_SLACK_TITLE}
      className="rounded-md border"
    >
      <ChannelBotHeader
        providerId="slack"
        title={PLATFORM_SLACK_TITLE}
        description={description}
        status={status}
      >
        {action}
      </ChannelBotHeader>
    </div>
  )
}

/** Routing lives on the Channels page, so the platform card's actions go there. */
function ChannelsLink({
  href,
  children,
}: {
  href: string
  children: ReactNode
}) {
  return (
    <Button type="button" size="sm" variant="outline" asChild>
      <Link href={href}>{children}</Link>
    </Button>
  )
}
