"use client"

import { Loader2 } from "lucide-react"
import { useState } from "react"
import { ChannelAccessSelect } from "@/components/agents/external-channels/channel-access-select"
import {
  ChannelBotHeader,
  ChannelBotLifecycleActions,
  ChannelBotNotice,
} from "@/components/agents/external-channels/channel-bot-card"
import {
  CHANNEL_BOT_STATES,
  channelBotReturnUrl,
  goToChannelBotAuthorization,
  platformInstallState,
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

  let title = "Slack"
  let description =
    "Mention @Tracecat in a channel and an agent answers in the thread. Replies, approvals and the session stay in sync with Tracecat."
  if (installed) {
    title = "Connected to Slack"
    description =
      "New threads go to the default agent, or to the agent set for their channel."
  }

  return (
    <div role="group" aria-label="Tracecat for Slack">
      <ChannelBotHeader
        providerId="slack"
        title={title}
        description={description}
        state={state}
      >
        {!installed || state === "needs_reconnect" ? (
          <Button
            type="button"
            size="sm"
            onClick={addToSlack}
            disabled={
              busy || !canUpdateAgent || (!installed && !defaultAgentId)
            }
          >
            {installPlatformBotIsPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            {installed ? "Reconnect" : "Add to Slack"}
          </Button>
        ) : null}
      </ChannelBotHeader>
      {workspaceClaimed && !installed ? (
        <div className="px-4 pb-4">
          <ChannelBotNotice title="This Slack workspace is already connected to another Tracecat workspace">
            A Slack workspace can belong to one Tracecat workspace. Ask that
            workspace&apos;s admin to disconnect it, or pick another Slack
            workspace.
          </ChannelBotNotice>
        </div>
      ) : null}
      {installed ? null : (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t p-4">
          <div className="min-w-48 flex-1">
            <p className="text-xs font-medium">Default agent</p>
            <p className="text-xs text-muted-foreground">
              Answers DMs and every channel. Pick one before you add Tracecat to
              Slack.
            </p>
          </div>
          <AgentSelect
            label="Default agent"
            value={defaultAgentId}
            agents={agents}
            disabled={busy || !canUpdateAgent}
            onChange={setChosenAgentId}
          />
        </div>
      )}
      {install && installed ? (
        <div className="space-y-6 border-t p-4">
          <PlatformChannelRoutes
            workspaceId={workspaceId}
            install={install}
            canUpdate={canUpdateAgent}
          />
          <ChannelAccessSelect workspaceId={workspaceId} bot={install} />
          {canUpdateAgent ? (
            <ChannelBotLifecycleActions
              workspaceId={workspaceId}
              bot={install}
              state={state}
              disconnectDescription="Tracecat stops answering in this Slack workspace until you add it again, and another Tracecat workspace can then add it there."
            />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
