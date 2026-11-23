"use client"

import type { ReactNode } from "react"

import { SlackPlatformCard } from "@/components/agents/external-channels/slack-platform-card"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  useChannelBotInstallResult,
  useSlackPlatformAppAvailable,
} from "@/hooks/use-agent-channel-bots"
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail } from "@/lib/errors"

/** The workspace's Channels page: Tracecat for Slack, where available. */
export function WorkspaceChannelsView({
  workspaceId,
}: {
  workspaceId: string
}) {
  const installFailure = useChannelBotInstallResult()
  const { slackPlatformAvailable, slackPlatformIsLoading, slackPlatformError } =
    useSlackPlatformAppAvailable(workspaceId)
  const { hasEntitlement } = useEntitlements()

  if (slackPlatformIsLoading) {
    return <CenteredSpinner />
  }

  let body: ReactNode
  if (slackPlatformError) {
    body = (
      <p className="text-sm text-destructive">
        {getApiErrorDetail(slackPlatformError) ??
          "Failed to load Tracecat for Slack."}
      </p>
    )
  } else if (slackPlatformAvailable) {
    body = (
      <div className="rounded-md border">
        <SlackPlatformCard
          workspaceId={workspaceId}
          workspaceClaimed={installFailure === "slack_workspace_claimed"}
        />
      </div>
    )
  } else if (hasEntitlement("agent_channels_preview")) {
    body = (
      <p className="text-sm text-muted-foreground">
        Tracecat for Slack isn&apos;t set up on this instance yet. A platform
        admin sets it up in Admin › Slack app.
      </p>
    )
  } else {
    body = (
      <p className="text-sm text-muted-foreground">
        Tracecat for Slack is not available in this workspace.
      </p>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-8">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Channels</h2>
            <p className="text-base text-muted-foreground">
              Add Tracecat to Slack and pick which agent answers each channel.
            </p>
          </div>
        </div>
        {body}
      </div>
    </div>
  )
}
