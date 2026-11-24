"use client"

import { ArrowUpRight, Loader2 } from "lucide-react"
import Link from "next/link"
import { type ReactNode, useState } from "react"
import type { AgentChannelBotRead, AgentPresetReadMinimal } from "@/client"
import {
  APPROVER_KINDS,
  approverDetail,
  DEFAULT_APPROVER_RULE,
} from "@/components/agents/agent-preset-rail-approvers"
import { LogoTile } from "@/components/agents/agent-preset-rail-primitives"
import { ChannelAccessSelect } from "@/components/agents/external-channels/channel-access-select"
import {
  ChannelBotLifecycleActions,
  ChannelBotStatusDot,
} from "@/components/agents/external-channels/channel-bot-card"
import {
  type AgentOption,
  AgentSelect,
  CustomBadge,
  withCurrentAgent,
} from "@/components/agents/external-channels/channel-bot-fields"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  channelBotDetails,
  channelBotReturnUrl,
  channelBotState,
  channelConventions,
  goToChannelBotAuthorization,
  slackUpdateNeeded,
} from "@/components/agents/external-channels/channel-bot-utils"
import { ChannelIcon } from "@/components/agents/external-channels/channel-picker"
import { ChannelsSideSheet } from "@/components/agents/external-channels/channels-side-sheet"
import {
  type ChannelTableRow,
  channelBotTitle,
  channelCount,
} from "@/components/agents/external-channels/workspace-channels-rows"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import {
  useChannelBotsBusy,
  useInstallPlatformChannelBot,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresetApprovers } from "@/hooks/use-agent-preset-approvers"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"

const PLATFORM_DISCONNECT =
  "Tracecat stops answering in this Slack workspace until you add it again, and another Tracecat workspace can then add it there."

/** The agent a bot answers with, who may talk to it and approve, and its connection. */
export function ChannelBotManageSheet({
  workspaceId,
  bot,
  rows,
  onClose,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
  /** The bot's rows in the table, unfiltered. */
  rows: ChannelTableRow[]
  onClose: () => void
}) {
  const platform = bot.app_owner === "platform"
  const title = channelBotTitle(bot)
  const { presets: allPresets } = useAgentPresets(workspaceId)
  const agentName =
    allPresets?.find((preset) => preset.id === bot.agent_preset_id)?.name ?? ""
  const { hasEntitlement } = useEntitlements()
  const busy = useChannelBotsBusy(workspaceId)
  return (
    <ChannelsSideSheet
      label={`Manage ${title}`}
      providerId={bot.provider}
      title={title}
      badge={platform ? null : <CustomBadge />}
      onClose={onClose}
      // Closing mid-request would drop a Reconnect's redirect.
      closeDisabled={busy}
    >
      <div className="space-y-5">
        <AgentSection
          workspaceId={workspaceId}
          bot={bot}
          title={title}
          agentName={agentName}
          channels={rows.filter((row) => row.kind !== "default")}
          allPresets={allPresets ?? []}
        />
        <Separator />
        <ChannelAccessSelect workspaceId={workspaceId} bot={bot} />
        {hasEntitlement("rbac_addons") ? (
          <>
            <Separator />
            <ApproversSection
              workspaceId={workspaceId}
              presetId={bot.agent_preset_id}
              agentName={agentName}
            />
          </>
        ) : null}
        <Separator />
        <ConnectionSection
          workspaceId={workspaceId}
          bot={bot}
          onDisconnected={onClose}
        />
      </div>
    </ChannelsSideSheet>
  )
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <p className="text-xs font-semibold">{children}</p>
}

/** The bot's agent; a custom app's switch is confirmed, since it moves every channel. */
function AgentSection({
  workspaceId,
  bot,
  title,
  agentName,
  channels,
  allPresets,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
  title: string
  agentName: string
  channels: ChannelTableRow[]
  allPresets: AgentPresetReadMinimal[]
}) {
  const platform = bot.app_owner === "platform"
  const { presets: chatPresets } = useAgentPresets(workspaceId, {
    useInChat: true,
  })
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, bot.provider)
  const busy = useChannelBotsBusy(workspaceId)
  const canUpdate = useScopeCheck("agent:update") === true
  const [switchTo, setSwitchTo] = useState<AgentOption | null>(null)
  // The current agent stays pickable after Use in chat is turned off.
  const agents = withCurrentAgent(
    (platform ? chatPresets : allPresets) ?? [],
    allPresets.find((preset) => preset.id === bot.agent_preset_id)
  )

  function apply(agentId: string): void {
    const providerName = CHANNEL_BOT_PROVIDER_NAMES[bot.provider]
    updateBot(
      { botId: bot.id, requestBody: { agent_preset_id: agentId } },
      {
        onSuccess: () =>
          toast({
            title: platform
              ? `Default ${providerName} agent changed`
              : `${title} agent changed`,
          }),
      }
    )
  }

  function choose(agentId: string): void {
    if (agentId === bot.agent_preset_id) {
      return
    }
    const next = agents.find((agent) => agent.id === agentId)
    if (!platform && next && channels.length > 0) {
      setSwitchTo(next)
      return
    }
    apply(agentId)
  }

  return (
    <div className="space-y-2">
      <SectionTitle>{platform ? "Default agent" : "Agent"}</SectionTitle>
      <AgentSelect
        label={platform ? "Default agent" : "Agent"}
        value={bot.agent_preset_id}
        agents={agents}
        disabled={busy || !canUpdate}
        onChange={choose}
        className="w-full"
      />
      <p className="text-xs text-muted-foreground">
        {platform
          ? "Answers DMs and channels without their own agent. Lists agents with Use in chat on."
          : "Answers everywhere this bot is added."}
      </p>
      <AlertDialog
        open={switchTo !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSwitchTo(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Switch {title} to {switchTo?.name}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {title} answers in {channelCount(channels.length)}.{" "}
              {switchTo?.name} answers there from the next message; threads
              already started keep {agentName}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul className="max-h-48 overflow-y-auto rounded-md border px-2 py-1">
            {channels.map((row) => (
              <li
                key={row.key}
                className="flex h-7 items-center gap-1.5 text-xs"
              >
                <ChannelIcon
                  channel={
                    row.kind === "default"
                      ? undefined
                      : {
                          id: row.channel.provider_channel_id,
                          name: row.label,
                          is_private: row.channel.is_private === true,
                          is_member: row.channel.is_member === true,
                        }
                  }
                  conventions={channelConventions(bot.provider)}
                />
                <span className="truncate">{row.label}</span>
              </li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (switchTo) {
                  apply(switchTo.id)
                }
              }}
            >
              Switch agent
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** Who decides the agent's approvals; edited on the agent, read here. */
function ApproversSection({
  workspaceId,
  presetId,
  agentName,
}: {
  workspaceId: string
  presetId: string
  agentName: string
}) {
  const { approvers, isError, isPending } = useAgentPresetApprovers(
    workspaceId,
    presetId
  )
  let body: ReactNode = (
    <p className="text-xs text-muted-foreground">{DEFAULT_APPROVER_RULE}</p>
  )
  if (isError) {
    body = (
      <p className="text-xs text-muted-foreground">
        Approvers could not be loaded.
      </p>
    )
  } else if (isPending) {
    body = <p className="text-xs text-muted-foreground">Loading approvers...</p>
  } else if (approvers.length > 0) {
    body = (
      <ul className="space-y-0.5">
        {approvers.map((approver) => {
          const { Icon } = APPROVER_KINDS[approver.kind]
          return (
            <li
              key={`${approver.kind}:${approver.id}`}
              className="flex h-[30px] min-w-0 items-center gap-2 px-1 text-xs"
            >
              <LogoTile>
                <Icon />
              </LogoTile>
              <span className="truncate">{approver.name}</span>
              <span className="truncate text-muted-foreground">
                {approverDetail(approver)}
              </span>
            </li>
          )
        })}
      </ul>
    )
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <SectionTitle>Approvers</SectionTitle>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs text-muted-foreground"
          asChild
        >
          <Link href={`/workspaces/${workspaceId}/agents/${presetId}`}>
            Edit on {agentName || "the agent"}
            <ArrowUpRight className="ml-1 size-3.5" />
          </Link>
        </Button>
      </div>
      {body}
      <p className="text-xs text-muted-foreground">Set per agent.</p>
    </div>
  )
}

function permissionsLabel(missing: number, updateNeeded: boolean): string {
  if (missing > 0) {
    return `${missing} missing`
  }
  return updateNeeded ? "Update needed" : "Up to date"
}

/** Status, tenant, transport and scopes, then reconnect, pause and disconnect. */
function ConnectionSection({
  workspaceId,
  bot,
  onDisconnected,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
  onDisconnected: () => void
}) {
  const platform = bot.app_owner === "platform"
  const details = channelBotDetails(bot, "slack")
  const state = channelBotState(bot, bot.provider_app_id != null)
  const missing = details?.missing_scopes.length ?? 0
  const updateNeeded = slackUpdateNeeded(details, state)
  const canUpdate = useScopeCheck("agent:update") === true
  const busy = useChannelBotsBusy(workspaceId)
  const { installPlatformBot, installPlatformBotIsPending } =
    useInstallPlatformChannelBot(workspaceId, bot.provider)
  const urgent = updateNeeded || state === "needs_reconnect"

  function reconnectPlatform(): void {
    installPlatformBot(
      {
        provider: bot.provider,
        return_url: channelBotReturnUrl(),
        bot_id: bot.id,
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

  let reconnect: ReactNode
  if (platform) {
    reconnect = (
      <Button
        type="button"
        size="sm"
        variant={urgent ? "default" : "outline"}
        onClick={reconnectPlatform}
        disabled={busy}
      >
        {installPlatformBotIsPending ? (
          <Loader2 className="mr-2 size-4 animate-spin" />
        ) : null}
        Reconnect
      </Button>
    )
  } else {
    // A custom app's credentials and manifest live on its agent's Channels tab.
    reconnect = (
      <Button
        type="button"
        size="sm"
        variant={urgent ? "default" : "outline"}
        asChild
      >
        <Link
          href={`/workspaces/${workspaceId}/agents/${bot.agent_preset_id}?tab=channels`}
        >
          {updateNeeded ? "Update in Slack" : "Reconnect"}
        </Link>
      </Button>
    )
  }

  return (
    <div className="space-y-2">
      <SectionTitle>Connection</SectionTitle>
      <dl className="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5 text-xs">
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <ChannelBotStatusDot state={state} updateNeeded={updateNeeded} />
        </dd>
        <dt className="text-muted-foreground">Workspace</dt>
        <dd className="truncate font-mono">{bot.provider_tenant_id ?? "—"}</dd>
        <dt className="text-muted-foreground">Events</dt>
        <dd>{details?.transport === "socket" ? "Socket Mode" : "HTTP"}</dd>
        <dt className="text-muted-foreground">Permissions</dt>
        <dd
          className="truncate"
          title={details?.missing_scopes.join(", ") || undefined}
        >
          {permissionsLabel(missing, updateNeeded)}
        </dd>
      </dl>
      {canUpdate ? (
        <div className="mt-2 border-t pt-3">
          <ChannelBotLifecycleActions
            workspaceId={workspaceId}
            bot={bot}
            state={state}
            onDisconnected={onDisconnected}
            disconnectDescription={platform ? PLATFORM_DISCONNECT : undefined}
            layout="split"
            leading={reconnect}
          />
        </div>
      ) : null}
    </div>
  )
}
