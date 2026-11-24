"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import {
  type ColumnDef,
  type ColumnFiltersState,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getSortedRowModel,
  type SortingState,
  type Table as TanstackTable,
  useReactTable,
} from "@tanstack/react-table"
import {
  Check,
  ChevronDown,
  CircleCheck,
  CircleDashed,
  CirclePause,
  type LucideIcon,
  MousePointerClickIcon,
  Plus,
  SlidersHorizontalIcon,
  Trash2,
  TriangleAlert,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import type {
  AgentChannelBotRead,
  AgentPresetReadMinimal,
  ChannelBotListingFailure,
} from "@/client"
import { ChannelBotStatus } from "@/components/agents/external-channels/channel-bot-card"
import { StatusLabel } from "@/components/agents/external-channels/channel-bot-fields"
import {
  channelBotDetails,
  channelBotState,
  channelConventions,
  slackUpdateNeeded,
} from "@/components/agents/external-channels/channel-bot-utils"
import { ChannelIcon } from "@/components/agents/external-channels/channel-picker"
import {
  AddChannelRouteDialog,
  AgentSelect,
} from "@/components/agents/external-channels/platform-channel-routes"
import {
  botSummary,
  type ChannelRowStatus,
  type ChannelsGroupBy,
  type ChannelTableRow,
  channelCount,
} from "@/components/agents/external-channels/workspace-channels-rows"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { DataTableColumnHeader } from "@/components/data-table"
import { DataTableFacetedFilter } from "@/components/data-table/faceted-filter"
import { ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useChannelBotsBusy,
  useDeleteChannelBotRoute,
  useSetChannelBotRoute,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { cn } from "@/lib/utils"

const FACETS = [
  { column: "provider", title: "Provider" },
  { column: "app", title: "App" },
  { column: "agent", title: "Agent" },
  { column: "status", title: "Status" },
] as const

const GROUP_BY_OPTIONS: ReadonlyArray<[ChannelsGroupBy, string]> = [
  ["bot", "Bot"],
  ["agent", "Agent"],
  ["none", "None"],
]

const STATUS_ICONS: Partial<Record<ChannelRowStatus, LucideIcon>> = {
  Active: CircleCheck,
  "Not in channel": CircleDashed,
  Unknown: CircleDashed,
  Paused: CirclePause,
  "Needs reconnect": TriangleAlert,
}

function matchesSearch(label: string, search: unknown): boolean {
  const q = String(search ?? "")
    .trim()
    .toLowerCase()
    .replace(/^#/, "")
  return label.toLowerCase().includes(q)
}

/** A facet column over one row field; facets match any picked value. */
function facetColumn(
  id: string,
  value: (row: ChannelTableRow) => string,
  sortable = true
): ColumnDef<ChannelTableRow> {
  return {
    id,
    accessorFn: value,
    filterFn: "arrIncludesSome",
    enableSorting: sortable,
    enableHiding: false,
  }
}

const COLUMNS: ColumnDef<ChannelTableRow>[] = [
  {
    id: "channel",
    accessorFn: (row) => row.label,
    filterFn: (row, _id, value) => matchesSearch(row.original.label, value),
    enableHiding: false,
  },
  facetColumn("agent", (row) => row.agentName),
  facetColumn("status", (row) => row.status),
  facetColumn("bot", (row) => row.botTitle),
  facetColumn("provider", (row) => row.provider, false),
  facetColumn("app", (row) => row.app, false),
]

/** The distinct values of a row field, as facet options. */
function facetOptions(
  rows: ChannelTableRow[],
  column: (typeof FACETS)[number]["column"]
): { label: string; value: string }[] {
  const pick: Record<typeof column, (row: ChannelTableRow) => string> = {
    provider: (row) => row.provider,
    app: (row) => row.app,
    agent: (row) => row.agentName,
    status: (row) => row.status,
  }
  return [...new Set(rows.map(pick[column]))]
    .sort()
    .map((value) => ({ label: value, value }))
}

/** Rows of one group, the install's default first. */
function defaultFirst(rows: ChannelTableRow[]): ChannelTableRow[] {
  return [
    ...rows.filter((row) => row.route === null),
    ...rows.filter((row) => row.route !== null),
  ]
}

/**
 * Every bot's channels, filtered, sorted and grouped in the browser: the rows
 * come from every page of the workspace routes.
 */
export function WorkspaceChannelsTable({
  workspaceId,
  bots,
  rows,
  chatAgents,
  routesLoading,
  routesError,
  listingFailures,
  managingBotId,
  onManage,
}: {
  workspaceId: string
  /** Installed bots, newest first. */
  bots: AgentChannelBotRead[]
  rows: ChannelTableRow[]
  /** Agents a Tracecat channel can be given. */
  chatAgents: AgentPresetReadMinimal[]
  routesLoading: boolean
  routesError: TracecatApiError | null
  /** Running bots Slack would not list, by bot ID. */
  listingFailures: Map<string, ChannelBotListingFailure>
  managingBotId: string | null
  onManage: (botId: string) => void
}) {
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [sorting, setSorting] = useState<SortingState>([])
  const [groupBy, setGroupBy] = useState<ChannelsGroupBy>("bot")
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [addingTo, setAddingTo] = useState<AgentChannelBotRead | null>(null)
  const canUpdate = useScopeCheck("agent:update") === true
  const table = useReactTable({
    data: rows,
    columns: COLUMNS,
    state: { columnFilters, sorting },
    onColumnFiltersChange: setColumnFilters,
    onSortingChange: setSorting,
    getRowId: (row) => row.key,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
  })
  const shown = table.getRowModel().rows.map((row) => row.original)
  const filtering = columnFilters.length > 0
  const search = (table.getColumn("channel")?.getFilterValue() as string) ?? ""

  function toggle(key: string): void {
    setCollapsed((previous) => {
      const next = new Set(previous)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const context: RowContext = {
    workspaceId,
    chatAgents,
    canUpdate,
    managingBotId,
    onManage,
  }
  let body: ReactNode
  if (groupBy === "bot") {
    body = bots.map((bot) => {
      const groupRows = defaultFirst(
        shown.filter((row) => row.bot.id === bot.id)
      )
      if (filtering && groupRows.length === 0) {
        return null
      }
      const key = `bot:${bot.id}`
      return (
        <BotGroup
          key={bot.id}
          bot={bot}
          rows={groupRows}
          allRows={rows.filter((row) => row.bot.id === bot.id)}
          open={!collapsed.has(key)}
          onToggle={() => toggle(key)}
          filtering={filtering}
          loading={routesLoading}
          unavailable={routesError !== null}
          listingFailure={listingFailures.get(bot.id) ?? null}
          onAddChannel={() => setAddingTo(bot)}
          context={context}
        />
      )
    })
  } else if (groupBy === "agent") {
    const byAgent = new Map<string, ChannelTableRow[]>()
    for (const row of shown) {
      byAgent.set(row.agentName, [...(byAgent.get(row.agentName) ?? []), row])
    }
    body = [...byAgent.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([agentName, agentRows]) => {
        const key = `agent:${agentName}`
        return (
          <AgentGroup
            key={key}
            agentName={agentName}
            rows={agentRows}
            open={!collapsed.has(key)}
            onToggle={() => toggle(key)}
            context={context}
          />
        )
      })
  } else {
    body = shown.map((row) => (
      <ChannelTableRowView key={row.key} row={row} mode="none" {...context} />
    ))
  }
  const columns = groupBy === "none" ? 5 : 4
  const nothingMatches = filtering && shown.length === 0

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <Input
            type="search"
            placeholder="Search channels..."
            aria-label="Search channels"
            value={search}
            onChange={(event) =>
              table.getColumn("channel")?.setFilterValue(event.target.value)
            }
            className="h-8 w-[150px] text-xs lg:w-[250px]"
          />
          {FACETS.map(({ column, title }) => (
            <DataTableFacetedFilter
              key={column}
              column={table.getColumn(column)}
              title={title}
              options={facetOptions(rows, column)}
            />
          ))}
          {filtering ? (
            <Button
              variant="ghost"
              onClick={() => table.resetColumnFilters()}
              className="h-8 px-2 text-xs text-foreground/80 lg:px-3"
            >
              Reset
              <Cross2Icon className="ml-2 size-4" />
            </Button>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {channelCount(shown.filter((row) => row.route !== null).length)}
          </span>
          <DisplayMenu groupBy={groupBy} onChange={setGroupBy} />
        </div>
      </div>
      <div className="rounded-md border">
        <Table className="text-xs" aria-label="Channels">
          <TableHeader>
            <HeaderRow table={table} groupBy={groupBy} />
          </TableHeader>
          <TableBody>
            {body}
            {nothingMatches ? (
              <TableRow>
                <TableCell
                  colSpan={columns}
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
      <p className="text-xs text-muted-foreground">
        Threads already started keep their agent.
      </p>
      {addingTo ? (
        <AddChannelRouteDialog
          workspaceId={workspaceId}
          install={addingTo}
          title={rows.find((row) => row.bot.id === addingTo.id)?.botTitle ?? ""}
          routedIds={
            new Set(
              rows
                .filter((row) => row.bot.id === addingTo.id && row.route)
                .map((row) => row.route?.provider_channel_id ?? "")
            )
          }
          agents={chatAgents}
          onClose={() => setAddingTo(null)}
        />
      ) : null}
    </div>
  )
}

/** Group by as the Display button's menu. */
function DisplayMenu({
  groupBy,
  onChange,
}: {
  groupBy: ChannelsGroupBy
  onChange: (groupBy: ChannelsGroupBy) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors hover:bg-muted/50",
            groupBy !== "bot" && "border-primary/50 bg-primary/5"
          )}
        >
          <SlidersHorizontalIcon className="size-3.5 text-muted-foreground" />
          <span>Display</span>
          <ChevronDown className="size-3 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-0 shadow-sm" align="end" sideOffset={4}>
        <div className="space-y-1.5 px-3 py-2.5">
          <p className="text-xs text-muted-foreground">Group by</p>
          <div role="menu" aria-label="Group by" className="space-y-0.5">
            {GROUP_BY_OPTIONS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="menuitemradio"
                aria-checked={groupBy === value}
                className="flex h-7 w-full items-center justify-between rounded-sm px-2 text-xs font-medium hover:bg-muted"
                onClick={() => {
                  onChange(value)
                  setOpen(false)
                }}
              >
                {label}
                {groupBy === value ? <Check className="size-3.5" /> : null}
              </button>
            ))}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** Sortable headers; the second column is the bot when grouped by agent. */
function HeaderRow({
  table,
  groupBy,
}: {
  table: TanstackTable<ChannelTableRow>
  groupBy: ChannelsGroupBy
}) {
  const ids: string[] = ["channel"]
  if (groupBy === "bot") {
    ids.push("agent")
  } else if (groupBy === "agent") {
    ids.push("bot")
  } else {
    ids.push("bot", "agent")
  }
  ids.push("status")
  const titles: Record<string, string> = {
    channel: "Channel",
    agent: "Agent",
    bot: "Bot",
    status: "Status",
  }
  const widths: Record<string, string> = {
    channel: groupBy === "none" ? "w-[32%]" : "w-[38%]",
    agent: groupBy === "none" ? "w-[20%]" : "w-[24%]",
    bot: groupBy === "none" ? "w-[20%]" : "w-[24%]",
  }
  return (
    <TableRow>
      {ids.map((id) => {
        const column = table.getColumn(id)
        return (
          <TableHead key={id} className={cn("h-8 text-xs", widths[id])}>
            {column ? (
              <DataTableColumnHeader column={column} title={titles[id]} />
            ) : null}
          </TableHead>
        )
      })}
      <TableHead className="h-8 w-[90px]" />
    </TableRow>
  )
}

type RowContext = {
  workspaceId: string
  chatAgents: AgentPresetReadMinimal[]
  canUpdate: boolean
  managingBotId: string | null
  onManage: (botId: string) => void
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
  context: RowContext
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={cn(
        "h-7 px-2.5 text-xs",
        context.managingBotId === botId && "bg-muted font-semibold"
      )}
      aria-pressed={context.managingBotId === botId}
      onClick={() => context.onManage(botId)}
    >
      Manage
    </Button>
  )
}

function BotName({ row }: { row: ChannelTableRow }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <ProviderIcon providerId={row.bot.provider} className="size-3.5 p-0" />
      <span className="truncate">{row.botTitle}</span>
      {row.platform ? null : <CustomBadge />}
    </div>
  )
}

function CustomBadge() {
  return (
    <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
      Custom
    </Badge>
  )
}

/** A bot's group: its row, then its channels, then a way to add one. */
function BotGroup({
  bot,
  rows,
  allRows,
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
  rows: ChannelTableRow[]
  allRows: ChannelTableRow[]
  open: boolean
  onToggle: () => void
  filtering: boolean
  loading: boolean
  unavailable: boolean
  /** Slack would not list this bot's channels. */
  listingFailure: ChannelBotListingFailure | null
  onAddChannel: () => void
  context: RowContext
}) {
  const platform = bot.app_owner === "platform"
  const title = allRows[0]?.botTitle ?? ""
  const state = channelBotState(bot, bot.provider_app_id != null)
  const updateNeeded = slackUpdateNeeded(channelBotDetails(bot, "slack"), state)
  const channelRows = allRows.filter((row) => row.route !== null)
  let note: ReactNode = null
  if (filtering) {
    note = null
  } else if (loading) {
    note = "Loading channels…"
  } else if (unavailable) {
    note = "Channels unavailable"
  } else if (listingFailure) {
    note = listingFailure.needs_reinstall
      ? "Slack won't list this bot's channels until its app is reinstalled."
      : "Slack couldn't list this bot's channels."
  } else if (!platform && channelRows.length === 0) {
    note =
      bot.status === "active"
        ? "Not in any channel yet. Invite it to one in Slack."
        : "Channels load while the bot is active."
  } else if (platform && bot.status === "active" && channelRows.length === 0) {
    const invite = channelConventions(bot.provider).inviteCommand
    note = invite ? (
      <>
        Next: run <code className="font-mono text-foreground">{invite}</code> in
        a channel, then give it its own agent.
      </>
    ) : null
  }
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
          {botSummary(bot, allRows)}
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
            <ChannelTableRowView
              key={row.key}
              row={row}
              mode="bot"
              {...context}
            />
          ))}
          {note ? (
            <TableRow>
              <TableCell
                colSpan={4}
                className="py-2 pl-8 text-muted-foreground"
              >
                {note}
              </TableCell>
            </TableRow>
          ) : null}
          {platform && context.canUpdate ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={4} className="py-1 pl-8">
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
function AgentGroup({
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
  context: RowContext
}) {
  const channels = rows.filter((row) => row.route !== null).length
  return (
    <>
      <TableRow className="bg-muted/40 hover:bg-muted/40">
        <TableCell colSpan={4} className="h-11 py-2">
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
            <ChannelTableRowView
              key={row.key}
              row={row}
              mode="agent"
              {...context}
            />
          ))
        : null}
    </>
  )
}

/** One channel, or a Tracecat install's "Everywhere else", in any grouping. */
function ChannelTableRowView({
  row,
  mode,
  workspaceId,
  chatAgents,
  canUpdate,
  managingBotId,
  onManage,
}: { row: ChannelTableRow; mode: ChannelsGroupBy } & RowContext) {
  const context = {
    workspaceId,
    chatAgents,
    canUpdate,
    managingBotId,
    onManage,
  }
  const indent = mode === "none" ? "" : "pl-8"
  let action: ReactNode = null
  if (mode !== "bot") {
    action = <ManageButton botId={row.bot.id} context={context} />
  } else if (row.platform && row.route && canUpdate) {
    action = <RemoveRouteButton workspaceId={workspaceId} row={row} />
  }
  return (
    <TableRow>
      <TableCell className={cn("py-2", indent)}>
        <ChannelName row={row} />
      </TableCell>
      {mode === "bot" ? null : (
        <TableCell className="py-2">
          <BotName row={row} />
        </TableCell>
      )}
      {mode === "agent" ? null : (
        <TableCell className="py-1.5">
          <AgentCell row={row} {...context} />
        </TableCell>
      )}
      <TableCell className="py-2">
        <RowStatus row={row} />
      </TableCell>
      <TableCell className="py-1.5 text-right">{action}</TableCell>
    </TableRow>
  )
}

function ChannelName({ row }: { row: ChannelTableRow }) {
  const { route } = row
  if (!route) {
    return (
      <span>
        <span className="italic">{row.label}</span>{" "}
        <span className="text-muted-foreground">DMs and unlisted channels</span>
      </span>
    )
  }
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <ChannelIcon
        channel={{
          id: route.provider_channel_id,
          name: row.label,
          is_private: route.is_private === true,
          is_member: route.is_member === true,
        }}
        conventions={channelConventions(route.provider)}
      />
      <span className={cn("truncate", !route.channel_name && "font-mono")}>
        {row.label}
      </span>
      {route.name_unavailable ? (
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
    row.status === "Not in channel" && row.platform && invite
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
  workspaceId,
  chatAgents,
  canUpdate,
}: { row: ChannelTableRow } & RowContext) {
  const busy = useChannelBotsBusy(workspaceId)
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, row.bot.provider)
  const { setRoute } = useSetChannelBotRoute(workspaceId, row.bot.provider)
  if (!row.platform || !canUpdate) {
    return <span>{row.agentName}</span>
  }
  // The current agent stays pickable after Use in chat is turned off.
  const agents = chatAgents.some((agent) => agent.id === row.agentId)
    ? chatAgents
    : [{ id: row.agentId, name: row.agentName }, ...chatAgents]

  function change(agentId: string): void {
    if (row.route === null) {
      updateBot({
        botId: row.bot.id,
        requestBody: { agent_preset_id: agentId },
      })
      return
    }
    setRoute({
      botId: row.bot.id,
      requestBody: {
        provider_channel_id: row.route.provider_channel_id,
        agent_preset_id: agentId,
      },
    })
  }

  return (
    <AgentSelect
      label={`Agent for ${row.label}`}
      value={row.agentId}
      agents={agents}
      disabled={busy}
      onChange={change}
      className="h-7 w-[150px]"
    />
  )
}

function RemoveRouteButton({
  workspaceId,
  row,
}: {
  workspaceId: string
  row: ChannelTableRow
}) {
  const busy = useChannelBotsBusy(workspaceId)
  const { deleteRoute } = useDeleteChannelBotRoute(
    workspaceId,
    row.bot.provider
  )
  const route = row.route
  if (!route) {
    return null
  }
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-7"
      aria-label={`Remove ${row.label}`}
      disabled={busy}
      onClick={() =>
        deleteRoute({
          botId: row.bot.id,
          providerChannelId: route.provider_channel_id,
        })
      }
    >
      <Trash2 className="size-3.5" />
    </Button>
  )
}
