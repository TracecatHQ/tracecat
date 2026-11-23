"use client"

import { CircleCheck, CircleDashed, Loader2 } from "lucide-react"
import { type ReactNode, useState } from "react"
import type {
  AgentChannelBotRead,
  AgentChannelWorkspaceRouteRead,
} from "@/client"
import { ChannelBotStatus } from "@/components/agents/external-channels/channel-bot-card"
import { StatusLabel } from "@/components/agents/external-channels/channel-bot-fields"
import {
  channelBotState,
  channelConventions,
  PLATFORM_SLACK_TITLE,
} from "@/components/agents/external-channels/channel-bot-utils"
import { ChannelIcon } from "@/components/agents/external-channels/channel-picker"
import { ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useChannelBotReadsAllowed,
  useWorkspaceChannelBots,
  useWorkspaceChannelRoutes,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { getApiErrorDetail } from "@/lib/errors"

const CUSTOM_SLACK_TITLE = "Custom Slack app"

function byChannelLabel(
  a: AgentChannelWorkspaceRouteRead,
  b: AgentChannelWorkspaceRouteRead
): number {
  return (a.channel_name ?? a.provider_channel_id).localeCompare(
    b.channel_name ?? b.provider_channel_id
  )
}

function channelCount(n: number): string {
  return n === 1 ? "1 channel" : `${n} channels`
}

/** One row per channel each bot answers in, grouped by bot, searchable by name. */
export function WorkspaceChannelsTable({
  workspaceId,
  empty,
}: {
  workspaceId: string
  /** Shown in place of the table once the workspace is known to have no bots. */
  empty: ReactNode
}) {
  const [search, setSearch] = useState("")
  const enabled = useChannelBotReadsAllowed()
  const { data: bots } = useWorkspaceChannelBots(workspaceId, enabled)
  const { presets } = useAgentPresets(workspaceId, { enabled })
  const {
    routes,
    routesIsLoading,
    routesError,
    searching,
    hasMoreRoutes,
    loadingMoreRoutes,
    loadMoreRoutes,
  } = useWorkspaceChannelRoutes(workspaceId, search, enabled)

  // A disconnected bot answers nowhere; a failed bot list shows in the card below.
  const shown = (bots ?? []).filter((bot) => bot.status !== "disconnected")
  if (!enabled || !bots) {
    return null
  }
  if (shown.length === 0) {
    return <>{empty}</>
  }

  const agentName = (id: string) =>
    presets?.find((preset) => preset.id === id)?.name ?? ""
  const filtering = search.trim() !== ""
  const groups = shown
    .map((bot) => ({
      bot,
      rows: (routes ?? [])
        .filter((route) => route.bot_id === bot.id)
        .sort(byChannelLabel),
    }))
    .filter((group) => !filtering || group.rows.length > 0)
  const total = (routes ?? []).length

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Input
            type="search"
            placeholder="Search channels..."
            aria-label="Search channels"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="h-8 w-[150px] text-xs lg:w-[250px]"
          />
          {searching ? (
            <Loader2
              className="size-3.5 animate-spin text-muted-foreground"
              aria-label="Searching"
            />
          ) : null}
        </div>
        {routes ? (
          <span className="text-xs text-muted-foreground">
            {channelCount(total)}
            {hasMoreRoutes ? "+" : ""}
          </span>
        ) : null}
      </div>
      <div className="rounded-md border">
        <Table className="text-xs" aria-label="Channels">
          <TableHeader>
            <TableRow>
              <TableHead className="h-8 w-[42%] text-xs">Channel</TableHead>
              <TableHead className="h-8 w-[28%] text-xs">Agent</TableHead>
              <TableHead className="h-8 text-xs">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.map(({ bot, rows }) => (
              <BotGroup
                key={bot.id}
                bot={bot}
                rows={rows}
                defaultAgent={agentName(bot.agent_preset_id)}
                showDefault={!filtering}
                loading={routesIsLoading}
                unavailable={routesError !== null && !routes}
              />
            ))}
            {routes && groups.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="h-16 text-center text-muted-foreground"
                >
                  No channels match. Invite a bot to the channel, then add it
                  here.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
      {routesError ? (
        <p className="text-xs text-destructive">
          {getApiErrorDetail(routesError) ?? "Failed to load the channels."}
        </p>
      ) : null}
      {hasMoreRoutes ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={loadMoreRoutes}
          disabled={loadingMoreRoutes}
        >
          {loadingMoreRoutes ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : null}
          Load more
        </Button>
      ) : null}
    </div>
  )
}

function BotGroup({
  bot,
  rows,
  defaultAgent,
  showDefault,
  loading,
  unavailable,
}: {
  bot: AgentChannelBotRead
  rows: AgentChannelWorkspaceRouteRead[]
  defaultAgent: string
  showDefault: boolean
  loading: boolean
  unavailable: boolean
}) {
  const platform = bot.app_owner === "platform"
  const running = bot.status === "active"
  const agents = new Set([
    ...(platform ? [bot.agent_preset_id] : []),
    ...rows.map((row) => row.agent_preset_id),
  ])
  const summary = platform
    ? `${channelCount(rows.length)} · ${agents.size === 1 ? "1 agent" : `${agents.size} agents`}`
    : `${channelCount(rows.length)} · ${defaultAgent}`
  let empty: ReactNode = null
  if (loading) {
    empty = "Loading channels…"
  } else if (unavailable) {
    empty = "Channels unavailable"
  } else if (!platform && rows.length === 0) {
    empty = running
      ? "Not in any channel yet. Invite it to one in Slack."
      : "Channels load while the bot is active."
  } else if (platform && running && showDefault && rows.length === 0) {
    const invite = channelConventions(bot.provider).inviteCommand
    empty = invite ? (
      <>
        Next: run <code className="font-mono text-foreground">{invite}</code> in
        a channel, then give it its own agent below.
      </>
    ) : null
  }
  return (
    <>
      <TableRow
        className="bg-muted/40 hover:bg-muted/40"
        aria-label={platform ? PLATFORM_SLACK_TITLE : CUSTOM_SLACK_TITLE}
      >
        <TableCell className="py-2">
          <div className="flex items-center gap-2">
            <ProviderIcon providerId={bot.provider} className="size-4 p-0" />
            <span className="font-medium">
              {platform ? PLATFORM_SLACK_TITLE : CUSTOM_SLACK_TITLE}
            </span>
            {platform ? null : (
              <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                Custom
              </Badge>
            )}
          </div>
        </TableCell>
        <TableCell className="py-2 text-muted-foreground">{summary}</TableCell>
        <TableCell className="py-2">
          <ChannelBotStatus
            state={channelBotState(bot, bot.provider_app_id != null)}
          />
        </TableCell>
      </TableRow>
      {platform && showDefault ? (
        <TableRow>
          <TableCell className="py-2 pl-8">
            <span className="italic">Everywhere else</span>{" "}
            <span className="text-muted-foreground">
              DMs and channels without their own agent
            </span>
          </TableCell>
          <TableCell className="py-2">{defaultAgent}</TableCell>
          <TableCell className="py-2 text-muted-foreground">Default</TableCell>
        </TableRow>
      ) : null}
      {rows.map((row) => (
        <ChannelRow
          key={row.provider_channel_id}
          row={row}
          running={running}
          platform={platform}
        />
      ))}
      {empty ? (
        <TableRow>
          <TableCell colSpan={3} className="py-2 pl-8 text-muted-foreground">
            {empty}
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}

function ChannelRow({
  row,
  running,
  platform,
}: {
  row: AgentChannelWorkspaceRouteRead
  running: boolean
  platform: boolean
}) {
  const conventions = channelConventions(row.provider)
  let status: ReactNode = null
  if (running && row.is_member === true) {
    status = <StatusLabel icon={CircleCheck}>Active</StatusLabel>
  } else if (running && row.is_member === false) {
    status = (
      <StatusLabel icon={CircleDashed}>
        {platform && conventions.inviteCommand
          ? `Not in channel · ${conventions.inviteCommand}`
          : "Not in channel"}
      </StatusLabel>
    )
  }
  return (
    <TableRow>
      <TableCell className="py-2 pl-8">
        <div className="flex min-w-0 items-center gap-1.5">
          <ChannelIcon
            channel={
              row.channel_name
                ? {
                    id: row.provider_channel_id,
                    name: row.channel_name,
                    is_private: row.is_private === true,
                    is_member: row.is_member === true,
                  }
                : undefined
            }
            conventions={conventions}
          />
          {row.channel_name ? (
            <span className="truncate">{row.channel_name}</span>
          ) : (
            <span className="truncate font-mono">
              {row.provider_channel_id}
            </span>
          )}
          {row.name_unavailable ? (
            <span className="text-muted-foreground">Name unavailable</span>
          ) : null}
        </div>
      </TableCell>
      <TableCell className="py-2">{row.agent_name}</TableCell>
      <TableCell className="py-2">{status}</TableCell>
    </TableRow>
  )
}
