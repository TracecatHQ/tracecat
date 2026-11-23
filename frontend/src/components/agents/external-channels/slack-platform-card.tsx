"use client"

import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { ChannelAccessSelect } from "@/components/agents/external-channels/channel-access-select"
import {
  ChannelBotActionsMenu,
  ChannelBotHeader,
  ChannelBotResumeButton,
  ChannelBotStatus,
} from "@/components/agents/external-channels/channel-bot-card"
import {
  CHANNEL_BOT_STATES,
  channelBotDetails,
  channelBotReturnUrl,
  goToChannelBotAuthorization,
  PLATFORM_SLACK_TITLE,
  platformInstallState,
  slackUpdateNeeded,
} from "@/components/agents/external-channels/channel-bot-utils"
import { PlatformChannelRoutes } from "@/components/agents/external-channels/platform-channel-routes"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import {
  useChannelBotsBusy,
  useInstallPlatformChannelBot,
  usePlatformChannelBots,
} from "@/hooks/use-agent-channel-bots"
import { getApiErrorDetail } from "@/lib/errors"

const ASK_TO_RECONNECT = "Ask someone who can edit agents to reconnect it."

/**
 * The workspace's Tracecat for Slack install, once added: its status, actions
 * and channel routes. Render only when the deployment registers the app.
 */
export function SlackPlatformCard({ workspaceId }: { workspaceId: string }) {
  const { installs, installsError } = usePlatformChannelBots(
    workspaceId,
    "slack",
    true
  )
  const { installPlatformBot, installPlatformBotIsPending } =
    useInstallPlatformChannelBot(workspaceId, "slack")
  const busy = useChannelBotsBusy(workspaceId)
  const canUpdateAgent = useScopeCheck("agent:update") === true

  if (!installs) {
    return installsError ? (
      <p className="text-sm text-destructive">
        {getApiErrorDetail(installsError) ??
          "Failed to load Tracecat for Slack."}
      </p>
    ) : null
  }
  const { install, state } = platformInstallState(installs)
  // Adding it lives in the page's Add bot menu and empty table.
  if (!install || !CHANNEL_BOT_STATES[state].installed) {
    return null
  }

  function reconnect(): void {
    if (!install) {
      return
    }
    installPlatformBot(
      {
        provider: "slack",
        return_url: channelBotReturnUrl(),
        bot_id: install.id,
        // A reconnect keeps the default agent.
        agent_preset_id: undefined,
      },
      {
        onSuccess: ({ authorization_url }) => {
          if (authorization_url) {
            goToChannelBotAuthorization(authorization_url)
          }
        },
      }
    )
  }

  const reconnectButton = (
    <Button type="button" size="sm" onClick={reconnect} disabled={busy}>
      {installPlatformBotIsPending ? (
        <Loader2 className="mr-2 size-4 animate-spin" />
      ) : null}
      Reconnect
    </Button>
  )

  const details = channelBotDetails(install, "slack")
  const missingScopes = details?.missing_scopes ?? []
  const updateNeeded = slackUpdateNeeded(details, state)

  let action: ReactNode = null
  let band: ReactNode = null
  // The menu offers Resume only when the header button doesn't.
  let headerResumes = false
  if (state === "needs_reconnect") {
    action = reconnectButton
    band = canUpdateAgent
      ? "Tracecat isn't answering in Slack. Reconnect to restore it."
      : `Tracecat isn't answering in Slack. ${ASK_TO_RECONNECT}`
  } else if (updateNeeded) {
    // Before paused: a reconnect requests every scope and activates the install.
    action = reconnectButton
    let next = ASK_TO_RECONNECT
    if (canUpdateAgent) {
      next =
        state === "paused"
          ? "Reconnect to approve them, which also resumes it."
          : "Reconnect to approve them. Tracecat stops answering until you finish in Slack."
    }
    band = (
      <>
        Slack hasn&apos;t granted every permission Tracecat needs. {next}
        {missingScopes.length > 0 ? (
          <span className="mt-1 block font-mono text-muted-foreground">
            Missing: {missingScopes.join(", ")}
          </span>
        ) : null}
      </>
    )
  } else if (state === "paused") {
    action = <ChannelBotResumeButton workspaceId={workspaceId} bot={install} />
    headerResumes = true
    band = canUpdateAgent
      ? "Tracecat ignores Slack messages until you resume."
      : "Tracecat ignores Slack messages until it's resumed. Ask someone who can edit agents to resume it."
  }

  return (
    <div
      role="group"
      aria-label={PLATFORM_SLACK_TITLE}
      className="rounded-md border"
    >
      <ChannelBotHeader
        providerId="slack"
        title={PLATFORM_SLACK_TITLE}
        description="New threads go to their channel's agent, or the default."
        status={<ChannelBotStatus state={state} updateNeeded={updateNeeded} />}
      >
        {canUpdateAgent ? (
          <>
            {action}
            <ChannelBotActionsMenu
              workspaceId={workspaceId}
              bot={install}
              state={state}
              offerResume={!headerResumes}
              disconnectDescription="Tracecat stops answering in this Slack workspace until you add it again, and another Tracecat workspace can then add it there."
            />
          </>
        ) : null}
      </ChannelBotHeader>
      {band ? (
        <div className="border-t p-4">
          <p className="text-xs">{band}</p>
        </div>
      ) : null}
      <div className="space-y-6 border-t p-4">
        <PlatformChannelRoutes
          workspaceId={workspaceId}
          install={install}
          canUpdate={canUpdateAgent}
        />
        <ChannelAccessSelect workspaceId={workspaceId} bot={install} />
      </div>
    </div>
  )
}
