"use client"

import { useState } from "react"
import {
  AddChannelBotMenu,
  AddTracecatSheet,
  type ChannelBotChoice,
  CustomSlackAppSheet,
  NoChannelBots,
  type PlatformOption,
} from "@/components/agents/external-channels/add-channel-bot"
import { ChannelBotNotice } from "@/components/agents/external-channels/channel-bot-card"
import { ChannelBotManageSheet } from "@/components/agents/external-channels/channel-bot-manage-sheet"
import {
  CHANNEL_BOT_STATES,
  platformInstallState,
} from "@/components/agents/external-channels/channel-bot-utils"
import {
  buildChannelRows,
  isShownBot,
} from "@/components/agents/external-channels/workspace-channels-rows"
import { WorkspaceChannelsTable } from "@/components/agents/external-channels/workspace-channels-table"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  useAllWorkspaceChannelRoutes,
  useChannelBotInstallResult,
  useChannelBotReadsAllowed,
  usePlatformChannelBots,
  useSlackPlatformAppAvailable,
  useWorkspaceChannelBots,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail } from "@/lib/errors"

/** What the right-hand sheet shows: a bot to manage, or a way to add one. */
type SheetTarget =
  | { kind: "manage"; botId: string }
  | { kind: ChannelBotChoice }

/** The workspace's Channels page: every bot's channels, with a side sheet to manage or add one. */
export function WorkspaceChannelsView({
  workspaceId,
}: {
  workspaceId: string
}) {
  const installFailure = useChannelBotInstallResult()
  const { slackPlatformAvailable, slackPlatformIsLoading, slackPlatformError } =
    useSlackPlatformAppAvailable(workspaceId)
  const { installs, installsError } = usePlatformChannelBots(
    workspaceId,
    "slack",
    slackPlatformAvailable
  )
  const { hasEntitlement } = useEntitlements()
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const enabled = useChannelBotReadsAllowed()
  const { data: bots, error: botsError } = useWorkspaceChannelBots(
    workspaceId,
    enabled
  )
  const { presets } = useAgentPresets(workspaceId, { enabled })
  const { presets: chatPresets } = useAgentPresets(workspaceId, {
    enabled,
    useInChat: true,
  })
  const {
    data: routesPage,
    error: routesError,
    isLoading: routesLoading,
  } = useAllWorkspaceChannelRoutes(workspaceId, enabled)
  const [sheet, setSheet] = useState<SheetTarget | null>(null)

  if (slackPlatformIsLoading) {
    return <CenteredSpinner />
  }

  const entitled = hasEntitlement("agent_channels_preview")
  const { install, state } = platformInstallState(installs)
  let platform: PlatformOption = slackPlatformError ? "failed" : "missing"
  // Tracecat is offered only once the install list says it isn't there yet.
  if (slackPlatformAvailable && installsError) {
    platform = "failed"
  } else if (slackPlatformAvailable && !installs) {
    platform = "checking"
  } else if (slackPlatformAvailable) {
    platform = CHANNEL_BOT_STATES[state].installed ? "installed" : "ready"
  }

  const shownBots = (bots ?? []).filter(isShownBot)
  const agentName = (id: string) =>
    presets?.find((preset) => preset.id === id)?.name ?? ""
  const rows = buildChannelRows(shownBots, routesPage?.items ?? [], agentName)
  const listingFailures = new Map(
    (routesPage?.listing_failures ?? []).map((f) => [f.bot_id, f] as const)
  )
  const managed =
    sheet?.kind === "manage"
      ? shownBots.find((bot) => bot.id === sheet.botId)
      : undefined

  let table = null
  if (enabled && botsError) {
    table = (
      <p className="text-sm text-destructive">
        {getApiErrorDetail(botsError) ?? "Failed to load the workspace's bots."}
      </p>
    )
  } else if (enabled && bots && shownBots.length === 0) {
    table = (
      <NoChannelBots
        platform={platform}
        canUpdateAgent={canUpdateAgent}
        onChoose={(kind) => setSheet({ kind })}
      />
    )
  } else if (enabled && bots) {
    table = (
      <WorkspaceChannelsTable
        workspaceId={workspaceId}
        bots={shownBots}
        rows={rows}
        chatAgents={chatPresets ?? []}
        routesLoading={routesLoading}
        routesError={routesError}
        listingFailures={listingFailures}
        managingBotId={managed?.id ?? null}
        onManage={(botId) => setSheet({ kind: "manage", botId })}
      />
    )
  }

  let side = null
  if (managed) {
    side = (
      <ChannelBotManageSheet
        key={managed.id}
        workspaceId={workspaceId}
        bot={managed}
        rows={rows.filter((row) => row.bot.id === managed.id)}
        onClose={() => setSheet(null)}
      />
    )
  } else if (sheet?.kind === "tracecat") {
    side = (
      <AddTracecatSheet
        workspaceId={workspaceId}
        install={install}
        onClose={() => setSheet(null)}
      />
    )
  } else if (sheet?.kind === "custom") {
    side = (
      <CustomSlackAppSheet
        workspaceId={workspaceId}
        onClose={() => setSheet(null)}
      />
    )
  }

  return (
    <div className="flex size-full">
      <div className="min-w-0 flex-1 overflow-auto">
        <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-8">
          <div className="flex w-full items-start justify-between gap-4">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">
                Channels
              </h2>
              <p className="text-base text-muted-foreground">
                Bots installed in this workspace and the agent that answers in
                each channel.
              </p>
            </div>
            {entitled && canUpdateAgent ? (
              <AddChannelBotMenu
                platform={platform}
                onChoose={(kind) => setSheet({ kind })}
              />
            ) : null}
          </div>
          {installFailure === "slack_workspace_claimed" ? (
            <ChannelBotNotice title="Another Tracecat workspace has that Slack workspace">
              Ask its admin to disconnect it, or choose a different Slack
              workspace when you try again.
            </ChannelBotNotice>
          ) : null}
          {table}
          {slackPlatformError ? (
            <p className="text-sm text-destructive">
              {getApiErrorDetail(slackPlatformError) ??
                "Failed to load Tracecat for Slack."}
            </p>
          ) : null}
          {entitled ? null : (
            <p className="text-sm text-muted-foreground">
              Tracecat for Slack is not available in this workspace.
            </p>
          )}
        </div>
      </div>
      {side}
    </div>
  )
}
