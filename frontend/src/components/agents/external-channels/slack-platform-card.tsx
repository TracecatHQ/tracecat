"use client"

import { Loader2 } from "lucide-react"
import { type ReactNode, useState } from "react"
import {
  ChannelBotActionsMenu,
  ChannelBotHeader,
  ChannelBotNotice,
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
import {
  AgentSelect,
  PlatformChannelRoutes,
} from "@/components/agents/external-channels/platform-channel-routes"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import {
  useChannelBotsBusy,
  useInstallPlatformChannelBot,
  usePlatformChannelBots,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { getApiErrorDetail } from "@/lib/errors"

const ASK_TO_RECONNECT = "Ask someone who can edit agents to reconnect it."

/**
 * The platform app for Slack: one install per workspace, answering new threads
 * with its default agent or a channel's own. Render only when the deployment
 * registers it.
 */
export function SlackPlatformCard({
  workspaceId,
  workspaceClaimed = false,
}: {
  workspaceId: string
  /** The last Add to Slack hit a Slack workspace another Tracecat workspace holds. */
  workspaceClaimed?: boolean
}) {
  const { installs, installsError } = usePlatformChannelBots(
    workspaceId,
    "slack",
    true
  )
  const { presets } = useAgentPresets(workspaceId)
  const { installPlatformBot, installPlatformBotIsPending } =
    useInstallPlatformChannelBot(workspaceId, "slack")
  const busy = useChannelBotsBusy(workspaceId)
  const canUpdateAgent = useScopeCheck("agent:update") === true
  const [chosenAgentId, setChosenAgentId] = useState<string | null>(null)

  if (!installs) {
    return installsError ? (
      <p className="p-4 text-sm text-destructive">
        {getApiErrorDetail(installsError) ??
          "Failed to load Tracecat for Slack."}
      </p>
    ) : null
  }
  const { install, state } = platformInstallState(installs)
  const { installed } = CHANNEL_BOT_STATES[state]
  const agents = presets ?? []
  // Adding a disconnected install again offers its last default agent.
  const previousAgentId =
    install && agents.some((agent) => agent.id === install.agent_preset_id)
      ? install.agent_preset_id
      : ""
  // A chosen agent deleted since counts as no choice.
  const chosenAgent = agents.some((agent) => agent.id === chosenAgentId)
    ? chosenAgentId
    : null
  const defaultAgentId = chosenAgent ?? previousAgentId

  function addToSlack(): void {
    installPlatformBot(
      {
        provider: "slack",
        return_url: channelBotReturnUrl(),
        bot_id: install?.id,
        // A reconnect keeps the default agent; a first install needs one.
        agent_preset_id: installed ? undefined : defaultAgentId,
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

  function installButton(label: string, disabled: boolean): ReactNode {
    return (
      <Button type="button" size="sm" onClick={addToSlack} disabled={disabled}>
        {installPlatformBotIsPending ? (
          <Loader2 className="mr-2 size-4 animate-spin" />
        ) : null}
        {label}
      </Button>
    )
  }

  if (!install || !installed) {
    return (
      <div role="group" aria-label={PLATFORM_SLACK_TITLE}>
        <ChannelBotHeader
          providerId="slack"
          title={PLATFORM_SLACK_TITLE}
          description="Mention @Tracecat in Slack and an agent answers in the thread."
          status={<ChannelBotStatus state={state} />}
        />
        <div className="space-y-3 border-t p-4">
          {workspaceClaimed ? (
            <ChannelBotNotice title="Another Tracecat workspace has that Slack workspace">
              Ask its admin to disconnect it, or choose a different Slack
              workspace when you try again.
            </ChannelBotNotice>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="min-w-48 flex-1">
              <p className="text-xs font-medium">Default agent</p>
              <p className="text-xs text-muted-foreground">
                Answers DMs and any channel you don&apos;t assign.
              </p>
            </div>
            {canUpdateAgent ? (
              <div className="flex flex-wrap items-center gap-2">
                <AgentSelect
                  label="Default agent"
                  value={defaultAgentId}
                  agents={agents}
                  disabled={busy}
                  onChange={setChosenAgentId}
                />
                {installButton("Add to Slack", busy || !defaultAgentId)}
              </div>
            ) : null}
          </div>
          {canUpdateAgent ? null : (
            <p className="text-xs">
              Ask someone who can edit agents to add Tracecat to Slack.
            </p>
          )}
        </div>
      </div>
    )
  }

  const details = channelBotDetails(install, "slack")
  const missingScopes = details?.missing_scopes ?? []
  const updateNeeded = slackUpdateNeeded(details, state)

  let action: ReactNode = null
  let band: ReactNode = null
  // The menu offers Resume only when the header button doesn't.
  let headerResumes = false
  if (state === "needs_reconnect") {
    action = installButton("Reconnect", busy)
    band = canUpdateAgent
      ? "Tracecat isn't answering in Slack. Reconnect to restore it."
      : `Tracecat isn't answering in Slack. ${ASK_TO_RECONNECT}`
  } else if (updateNeeded) {
    // Before paused: a reconnect requests every scope and activates the install.
    action = installButton("Reconnect", busy)
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
    <div role="group" aria-label={PLATFORM_SLACK_TITLE}>
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
      <div className="border-t p-4">
        <PlatformChannelRoutes
          workspaceId={workspaceId}
          install={install}
          canUpdate={canUpdateAgent}
        />
      </div>
    </div>
  )
}
