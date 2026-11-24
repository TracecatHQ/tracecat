"use client"

import {
  ChevronDown,
  CircleCheck,
  CircleDashed,
  CirclePause,
  type LucideIcon,
  MousePointerClickIcon,
  Plus,
  Trash2,
  TriangleAlert,
} from "lucide-react"
import type { ReactNode } from "react"
import type {
  AgentChannelBotRead,
  AgentPresetReadMinimal,
  ChannelBotListingFailure,
} from "@/client"
import { ChannelBotStatus } from "@/components/agents/external-channels/channel-bot-card"
import {
  AgentSelect,
  CustomBadge,
  StatusLabel,
  withCurrentAgent,
} from "@/components/agents/external-channels/channel-bot-fields"
import {
  channelBotDetails,
  channelBotState,
  channelConventions,
  slackUpdateNeeded,
} from "@/components/agents/external-channels/channel-bot-utils"
import { ChannelIcon } from "@/components/agents/external-channels/channel-picker"
import {
  botSummary,
  CHANNELS_LAYOUT,
  type ChannelRowStatus,
  type ChannelsGroupBy,
  type ChannelTableRow,
  channelBotTitle,
  channelCount,
  isPlatformRow,
} from "@/components/agents/external-channels/workspace-channels-rows"
import { ProviderIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { TableCell, TableRow } from "@/components/ui/table"
import {
  useChannelBotsBusy,
  useDeleteChannelBotRoute,
  useSetChannelBotRoute,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { cn } from "@/lib/utils"

const STATUS_ICONS: Partial<Record<ChannelRowStatus, LucideIcon>> = {
  Active: CircleCheck,
  "Not in channel": CircleDashed,
  Unknown: CircleDashed,
  Paused: CirclePause,
  "Needs reconnect": TriangleAlert,
}

/** What every row of the table needs from the page. */
export type ChannelsRowContext = {
  workspaceId: string
  /** Agents a Tracecat channel can be given. */
  chatAgents: AgentPresetReadMinimal[]
  canUpdate: boolean
  managingBotId: string | null
  onManage: (botId: string) => void
}

/** Header and row cells span the grouping's columns plus the action column. */
export function columnSpan(groupBy: ChannelsGroupBy): number {
  return CHANNELS_LAYOUT[groupBy].length + 1
}

function Chevron({
  open,
  label,
  onToggle,
}: {
  open: boolean
  label: string
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-label={label}
      onClick={onToggle}
      className="-ml-1 inline-flex size-5 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      <ChevronDown
        className={cn("size-3.5 transition-transform", !open && "-rotate-90")}
      />
    </button>
  )
}

function ManageButton({
  botId,
  context,
}: {
  botId: string
  context: ChannelsRowContext
}) {
  const busy = useChannelBotsBusy(context.workspaceId)
  const open = context.managingBotId === botId
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={cn("h-7 px-2.5 text-xs", open && "bg-muted font-semibold")}
      aria-pressed={open}
      // A switch mid-request would unmount the sheet and drop its redirect.
      disabled={busy}
      onClick={() => context.onManage(botId)}
    >
      Manage
    </Button>
  )
}

/** Why a bot's group has no channel rows to show, or null. */
function emptyNote(
  bot: AgentChannelBotRead,
  rows: ChannelTableRow[],
  {
    loading,
    unavailable,
    listingFailure,
  }: {
    loading: boolean
    unavailable: boolean
    listingFailure: ChannelBotListingFailure | null
  }
): ReactNode {
  const channels = rows.filter((row) => row.kind !== "default").length
  if (loading) {
    return "Loading channels…"
  }
  if (unavailable) {
    return "Channels unavailable"
  }
  if (listingFailure) {
    return listingFailure.needs_reinstall
      ? "Slack won't list this bot's channels until its app is reinstalled."
      : "Slack couldn't list this bot's channels."
  }
  if (channels > 0) {
    return null
  }
  if (bot.app_owner !== "platform") {
    return bot.status === "active"
      ? "Not in any channel yet. Invite it to one in Slack."
      : "Channels load while the bot is active."
  }
  const invite = channelConventions(bot.provider).inviteCommand
  if (bot.status !== "active" || !invite) {
    return null
  }
  return (
    <>
      Next: run <code className="font-mono text-foreground">{invite}</code> in a
      channel, then give it its own agent.
    </>
  )
}

/** A bot's group: its row, then its channels, then a way to add one. */
export function BotGroup({
  bot,
  rows,
  allRows,
  defaultAgentName,
  open,
  onToggle,
  filtering,
  loading,
  unavailable,
  listingFailure,
  onAddChannel,
  context,
}: {
  bot: AgentChannelBotRead
  /** The rows the filters leave, sorted. */
  rows: ChannelTableRow[]
  allRows: ChannelTableRow[]
  defaultAgentName: string
  open: boolean
  onToggle: () => void
  filtering: boolean
  loading: boolean
  unavailable: boolean
  /** Slack would not list this bot's channels. */
  listingFailure: ChannelBotListingFailure | null
  onAddChannel: () => void
  context: ChannelsRowContext
}) {
  const platform = bot.app_owner === "platform"
  const title = channelBotTitle(bot)
  const state = channelBotState(bot, bot.provider_app_id != null)
  const updateNeeded = slackUpdateNeeded(channelBotDetails(bot, "slack"), state)
  const note = filtering
    ? null
    : emptyNote(bot, allRows, { loading, unavailable, listingFailure })
  const span = columnSpan("bot")
  return (
    <>
      <TableRow className="bg-muted/40 hover:bg-muted/40" aria-label={title}>
        <TableCell className="h-11 py-2">
          <div className="flex items-center gap-2">
            <Chevron
              open={open}
              label={`Show channels of ${title}`}
              onToggle={onToggle}
            />
            <ProviderIcon providerId={bot.provider} className="size-4 p-0" />
            <span className="font-semibold">{title}</span>
            {platform ? null : <CustomBadge />}
          </div>
        </TableCell>
        <TableCell className="py-2 text-muted-foreground">
          {botSummary(bot, allRows, defaultAgentName)}
        </TableCell>
        <TableCell className="py-2">
          <ChannelBotStatus state={state} updateNeeded={updateNeeded} />
        </TableCell>
        <TableCell className="py-2 text-right">
          <ManageButton botId={bot.id} context={context} />
        </TableCell>
      </TableRow>
      {open ? (
        <>
          {rows.map((row) => (
            <ChannelRow
              key={row.key}
              row={row}
              groupBy="bot"
              context={context}
            />
          ))}
          {note ? (
            <TableRow>
              <TableCell
                colSpan={span}
                className="py-2 pl-8 text-muted-foreground"
              >
                {note}
              </TableCell>
            </TableRow>
          ) : null}
          {platform && context.canUpdate ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={span} className="py-1 pl-8">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 px-0 text-xs text-muted-foreground hover:bg-transparent"
                  onClick={onAddChannel}
                >
                  <Plus className="mr-1.5 size-3.5" />
                  Add channel to {title}
                </Button>
              </TableCell>
            </TableRow>
          ) : null}
        </>
      ) : null}
    </>
  )
}

/** An agent's group: the channels it answers in, across bots. */
export function AgentGroup({
  agentName,
  rows,
  open,
  onToggle,
  context,
}: {
  agentName: string
  rows: ChannelTableRow[]
  open: boolean
  onToggle: () => void
  context: ChannelsRowContext
}) {
  const channels = rows.filter((row) => row.kind !== "default").length
  return (
    <>
      <TableRow className="bg-muted/40 hover:bg-muted/40">
        <TableCell colSpan={columnSpan("agent")} className="h-11 py-2">
          <div className="flex items-center gap-2">
            <Chevron
              open={open}
              label={`Show channels for ${agentName}`}
              onToggle={onToggle}
            />
            <MousePointerClickIcon
              className="size-3.5 text-muted-foreground"
              aria-hidden
            />
            <span className="font-semibold">{agentName}</span>
            <span className="text-muted-foreground">
              {channelCount(channels)}
            </span>
          </div>
        </TableCell>
      </TableRow>
      {open
        ? rows.map((row) => (
            <ChannelRow
              key={row.key}
              row={row}
              groupBy="agent"
              context={context}
            />
          ))
        : null}
    </>
  )
}

/** One channel, or a Tracecat install's "Everywhere else", in any grouping. */
export function ChannelRow({
  row,
  groupBy,
  context,
}: {
  row: ChannelTableRow
  groupBy: ChannelsGroupBy
  context: ChannelsRowContext
}) {
  const cells: Record<string, ReactNode> = {
    channel: <ChannelName row={row} />,
    bot: <BotName row={row} />,
    agent: <AgentCell row={row} context={context} />,
    status: <RowStatus row={row} />,
  }
  let action: ReactNode = null
  if (groupBy !== "bot") {
    action = <ManageButton botId={row.bot.id} context={context} />
  } else if (row.kind === "routed" && isPlatformRow(row) && context.canUpdate) {
    action = (
      <RemoveRouteButton
        workspaceId={context.workspaceId}
        bot={row.bot}
        providerChannelId={row.channel.provider_channel_id}
        label={row.label}
      />
    )
  }
  return (
    <TableRow>
      {CHANNELS_LAYOUT[groupBy].map((column) => (
        <TableCell
          key={column}
          className={cn(
            column === "agent" ? "py-1.5" : "py-2",
            column === "channel" && groupBy !== "none" && "pl-8"
          )}
        >
          {cells[column]}
        </TableCell>
      ))}
      <TableCell className="py-1.5 text-right">{action}</TableCell>
    </TableRow>
  )
}

function BotName({ row }: { row: ChannelTableRow }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <ProviderIcon providerId={row.bot.provider} className="size-3.5 p-0" />
      <span className="truncate">{row.botTitle}</span>
      {isPlatformRow(row) ? null : <CustomBadge />}
    </div>
  )
}

function ChannelName({ row }: { row: ChannelTableRow }) {
  if (row.kind === "default") {
    return (
      <span>
        <span className="italic">{row.label}</span>{" "}
        <span className="text-muted-foreground">DMs and unlisted channels</span>
      </span>
    )
  }
  const { channel } = row
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <ChannelIcon
        channel={{
          id: channel.provider_channel_id,
          name: row.label,
          is_private: channel.is_private === true,
          is_member: channel.is_member === true,
        }}
        conventions={channelConventions(channel.provider)}
      />
      <span className={cn("truncate", !channel.channel_name && "font-mono")}>
        {row.label}
      </span>
      {channel.name_unavailable ? (
        <span className="shrink-0 text-muted-foreground">Name unavailable</span>
      ) : null}
    </div>
  )
}

function RowStatus({ row }: { row: ChannelTableRow }) {
  const Icon = STATUS_ICONS[row.status]
  if (!Icon) {
    return <span className="text-muted-foreground">{row.status}</span>
  }
  const invite = channelConventions(row.bot.provider).inviteCommand
  const label =
    row.status === "Not in channel" && isPlatformRow(row) && invite
      ? `Not in channel · ${invite}`
      : row.status
  return (
    <StatusLabel icon={Icon} destructive={row.status === "Needs reconnect"}>
      {label}
    </StatusLabel>
  )
}

/** Tracecat's agents are picked per channel; a custom app's is set in Manage. */
function AgentCell({
  row,
  context,
}: {
  row: ChannelTableRow
  context: ChannelsRowContext
}) {
  const { workspaceId } = context
  const busy = useChannelBotsBusy(workspaceId)
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, row.bot.provider)
  const { setRoute } = useSetChannelBotRoute(workspaceId, row.bot.provider)
  if (!isPlatformRow(row) || !context.canUpdate) {
    return <span>{row.agentName}</span>
  }

  function change(agentId: string): void {
    if (row.kind === "default") {
      updateBot({
        botId: row.bot.id,
        requestBody: { agent_preset_id: agentId },
      })
      return
    }
    // An unrouted channel gets its own route here.
    setRoute({
      botId: row.bot.id,
      requestBody: {
        provider_channel_id: row.channel.provider_channel_id,
        agent_preset_id: agentId,
      },
    })
  }

  return (
    <AgentSelect
      label={`Agent for ${row.label}`}
      value={row.agentId}
      agents={withCurrentAgent(context.chatAgents, {
        id: row.agentId,
        name: row.agentName,
      })}
      disabled={busy}
      onChange={change}
      className="h-7 w-[150px]"
    />
  )
}

function RemoveRouteButton({
  workspaceId,
  bot,
  providerChannelId,
  label,
}: {
  workspaceId: string
  bot: AgentChannelBotRead
  providerChannelId: string
  label: string
}) {
  const busy = useChannelBotsBusy(workspaceId)
  const { deleteRoute } = useDeleteChannelBotRoute(workspaceId, bot.provider)
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-7"
      aria-label={`Remove ${label}`}
      disabled={busy}
      onClick={() => deleteRoute({ botId: bot.id, providerChannelId })}
    >
      <Trash2 className="size-3.5" />
    </Button>
  )
}
