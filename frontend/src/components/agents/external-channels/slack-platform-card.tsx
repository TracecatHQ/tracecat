"use client"

import { Loader2 } from "lucide-react"
import type { AgentPresetRead } from "@/client"
import { ChannelBotLifecycleActions } from "@/components/agents/external-channels/channel-bot-card"
import {
  CHANNEL_BOT_STATES,
  channelBotReturnUrl,
  channelBotState,
  goToChannelBotAuthorization,
  pickChannelBot,
} from "@/components/agents/external-channels/channel-bot-utils"
import { PlatformChannelRoutes } from "@/components/agents/external-channels/platform-channel-routes"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ProviderIcon } from "@/components/icons"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  useChannelBotPlatformApps,
  useChannelBotsBusy,
  useInstallPlatformChannelBot,
  usePlatformChannelBots,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"

/**
 * The platform app for Slack: one install per workspace, answering new threads
 * with its default agent. Shown only when the deployment registers it.
 */
export function SlackPlatformCard({
  workspaceId,
  preset,
  workspaceClaimed = false,
}: {
  workspaceId: string
  preset: AgentPresetRead
  /** The last Add to Slack hit a Slack workspace another Tracecat workspace holds. */
  workspaceClaimed?: boolean
}) {
  const { platformApps } = useChannelBotPlatformApps(workspaceId)
  const available = platformApps?.some((app) => app.provider === "slack")
  const { installs } = usePlatformChannelBots(
    workspaceId,
    "slack",
    available === true
  )
  const { installPlatformBot, installPlatformBotIsPending } =
    useInstallPlatformChannelBot(workspaceId, "slack")
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, "slack")
  const busy = useChannelBotsBusy(workspaceId)
  const canUpdateAgent = useScopeCheck("agent:update") === true

  if (!available || !installs) {
    return null
  }
  const install = pickChannelBot(installs)
  // Only a finished install records the Slack app.
  const state = channelBotState(install, install?.provider_app_id != null)
  const { label, installed } = CHANNEL_BOT_STATES[state]
  const isDefault = install?.agent_preset_id === preset.id

  function addToSlack(): void {
    installPlatformBot(
      {
        provider: "slack",
        return_url: channelBotReturnUrl(),
        bot_id: install?.id,
        // A reconnect keeps the default agent; adding again makes it this one.
        agent_preset_id: installed ? undefined : preset.id,
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

  function makeDefault(): void {
    if (!install) {
      return
    }
    updateBot(
      { botId: install.id, requestBody: { agent_preset_id: preset.id } },
      { onSuccess: () => toast({ title: "Default Slack agent changed" }) }
    )
  }

  let title = "Slack"
  let description =
    "Mention @Tracecat in a channel and this agent answers in the thread. Replies, approvals and the session stay in sync with Tracecat."
  if (installed) {
    title = "Connected to Slack"
    description = isDefault
      ? "This agent answers new Slack threads in this workspace."
      : "Installed for this workspace; another agent answers new threads."
  }

  return (
    <div
      className="rounded border"
      role="group"
      aria-label="Tracecat for Slack"
    >
      <div className="flex items-center gap-4 p-4">
        <div className="flex size-10 items-center justify-center rounded border bg-muted/30">
          <ProviderIcon providerId="slack" className="size-7 p-0.5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        {label ? <Badge variant="secondary">{label}</Badge> : null}
        {installed && !isDefault ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={makeDefault}
            disabled={busy || !canUpdateAgent}
          >
            Make default
          </Button>
        ) : null}
        {!installed || state === "needs_reconnect" ? (
          <Button
            type="button"
            size="sm"
            onClick={addToSlack}
            disabled={busy || !canUpdateAgent}
          >
            {installPlatformBotIsPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            {installed ? "Reconnect" : "Add to Slack"}
          </Button>
        ) : null}
      </div>
      {workspaceClaimed && !installed ? (
        <div className="border-t px-4 py-3">
          <Alert variant="destructive">
            <AlertTitle>
              This Slack workspace is already connected to another Tracecat
              workspace
            </AlertTitle>
            <AlertDescription>
              A Slack workspace can belong to one Tracecat workspace. Ask that
              workspace&apos;s admin to disconnect it, or pick another Slack
              workspace.
            </AlertDescription>
          </Alert>
        </div>
      ) : null}
      {install && installed && canUpdateAgent ? (
        <div className="border-t px-4 py-3">
          <ChannelBotLifecycleActions
            workspaceId={workspaceId}
            bot={install}
            state={state}
            disconnectDescription="Tracecat stops answering in this Slack workspace until you add it again, and another Tracecat workspace can then add it there."
          />
        </div>
      ) : null}
      {install && installed ? (
        <PlatformChannelRoutes
          workspaceId={workspaceId}
          install={install}
          canUpdate={canUpdateAgent}
        />
      ) : null}
    </div>
  )
}
