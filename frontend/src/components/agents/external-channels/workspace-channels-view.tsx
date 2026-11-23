"use client"

import { useState } from "react"
import {
  AddChannelBotMenu,
  AddTracecatDialog,
  type ChannelBotChoice,
  CustomSlackAppDialog,
  NoChannelBots,
  type PlatformOption,
} from "@/components/agents/external-channels/add-channel-bot"
import { ChannelBotNotice } from "@/components/agents/external-channels/channel-bot-card"
import {
  CHANNEL_BOT_STATES,
  platformInstallState,
} from "@/components/agents/external-channels/channel-bot-utils"
import { SlackPlatformCard } from "@/components/agents/external-channels/slack-platform-card"
import { WorkspaceChannelsTable } from "@/components/agents/external-channels/workspace-channels-table"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  useChannelBotInstallResult,
  usePlatformChannelBots,
  useSlackPlatformAppAvailable,
} from "@/hooks/use-agent-channel-bots"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail } from "@/lib/errors"

/** The workspace's Channels page: every bot's channels, then Tracecat for Slack. */
export function WorkspaceChannelsView({
  workspaceId,
}: {
  workspaceId: string
}) {
  const installFailure = useChannelBotInstallResult()
  const { slackPlatformAvailable, slackPlatformIsLoading, slackPlatformError } =
    useSlackPlatformAppAvailable(workspaceId)
  const { installs } = usePlatformChannelBots(
    workspaceId,
    "slack",
    slackPlatformAvailable
  )
  const { hasEntitlement } = useEntitlements()
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const [adding, setAdding] = useState<ChannelBotChoice | null>(null)

  if (slackPlatformIsLoading) {
    return <CenteredSpinner />
  }

  const entitled = hasEntitlement("agent_channels_preview")
  const { install, state } = platformInstallState(installs)
  let platform: PlatformOption = slackPlatformError ? "failed" : "missing"
  if (slackPlatformAvailable) {
    platform = CHANNEL_BOT_STATES[state].installed ? "installed" : "ready"
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-8">
        <div className="flex w-full items-start justify-between gap-4">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Channels</h2>
            <p className="text-base text-muted-foreground">
              Bots installed in this workspace and the agent that answers in
              each channel.
            </p>
          </div>
          {entitled && canUpdateAgent ? (
            <AddChannelBotMenu platform={platform} onChoose={setAdding} />
          ) : null}
        </div>
        {installFailure === "slack_workspace_claimed" ? (
          <ChannelBotNotice title="Another Tracecat workspace has that Slack workspace">
            Ask its admin to disconnect it, or choose a different Slack
            workspace when you try again.
          </ChannelBotNotice>
        ) : null}
        <WorkspaceChannelsTable
          workspaceId={workspaceId}
          empty={
            <NoChannelBots
              platform={platform}
              canUpdateAgent={canUpdateAgent}
              onChoose={setAdding}
            />
          }
        />
        {slackPlatformError ? (
          <p className="text-sm text-destructive">
            {getApiErrorDetail(slackPlatformError) ??
              "Failed to load Tracecat for Slack."}
          </p>
        ) : null}
        {slackPlatformAvailable ? (
          <SlackPlatformCard workspaceId={workspaceId} />
        ) : null}
        {entitled ? null : (
          <p className="text-sm text-muted-foreground">
            Tracecat for Slack is not available in this workspace.
          </p>
        )}
      </div>
      {adding === "tracecat" ? (
        <AddTracecatDialog
          workspaceId={workspaceId}
          install={install}
          onClose={() => setAdding(null)}
        />
      ) : null}
      {adding === "custom" ? (
        <CustomSlackAppDialog
          workspaceId={workspaceId}
          onClose={() => setAdding(null)}
        />
      ) : null}
    </div>
  )
}
