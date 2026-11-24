"use client"

import { Cross2Icon, PlusCircledIcon } from "@radix-ui/react-icons"
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
import { Check, ChevronDown, SlidersHorizontalIcon } from "lucide-react"
import {
  type ReactNode,
  type RefObject,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import type {
  AgentChannelBotRead,
  AgentPresetReadMinimal,
  ChannelBotListingFailure,
} from "@/client"
import { AddChannelRouteDialog } from "@/components/agents/external-channels/add-channel-route-dialog"
import {
  AgentGroup,
  BotGroup,
  ChannelRow,
  type ChannelsRowContext,
  columnSpan,
} from "@/components/agents/external-channels/workspace-channels-groups"
import {
  CHANNELS_LAYOUT,
  type ChannelsColumn,
  type ChannelsGroupBy,
  type ChannelTableRow,
  channelBotTitle,
  channelCount,
  FACETS,
  groupByAgent,
  groupByBot,
  matchesChannelSearch,
} from "@/components/agents/external-channels/workspace-channels-rows"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { DataTableColumnHeader } from "@/components/data-table"
import { DataTableFacetedFilter } from "@/components/data-table/faceted-filter"
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
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { cn } from "@/lib/utils"

const GROUP_BY_OPTIONS: ReadonlyArray<[ChannelsGroupBy, string]> = [
  ["bot", "Bot"],
  ["agent", "Agent"],
  ["none", "None"],
]

/** The search box never shrinks below this. */
const SEARCH_MIN_WIDTH = 160

const COLUMN_TITLES: Record<ChannelsColumn, string> = {
  channel: "Channel",
  agent: "Agent",
  bot: "Bot",
  status: "Status",
}

const COLUMNS: ColumnDef<ChannelTableRow>[] = [
  {
    id: "channel",
    accessorFn: (row) => row.label,
    filterFn: (row, _id, value) =>
      matchesChannelSearch(row.original, String(value ?? "")),
    enableHiding: false,
  },
  {
    id: "bot",
    accessorFn: (row) => row.botTitle,
    enableHiding: false,
  },
  // Facets match any picked value exactly; Provider and App only filter.
  ...FACETS.map(
    ({ column, value }): ColumnDef<ChannelTableRow> => ({
      id: column,
      accessorFn: value,
      filterFn: (row, id, picked: string[]) =>
        picked.includes(row.getValue<string>(id)),
      enableSorting: column === "agent" || column === "status",
      enableHiding: false,
    })
  ),
]

/**
 * Every bot's channels, filtered, sorted and grouped in the browser: the rows
 * come from every page of the workspace routes.
 */
export function WorkspaceChannelsTable({
  workspaceId,
  bots,
  rows,
  chatAgents,
  agentName,
  routesLoading,
  routesError,
  routesUnavailable,
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
  agentName: (id: string) => string
  routesLoading: boolean
  routesError: TracecatApiError | null
  /** No routes loaded; a failed refetch keeps the last ones. */
  routesUnavailable: boolean
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
  const toolbarRef = useRef<HTMLDivElement>(null)
  const facetsRef = useRef<HTMLDivElement>(null)
  const trailingRef = useRef<HTMLDivElement>(null)
  const compact = useFacetsOverflow(toolbarRef, facetsRef, trailingRef)
  const facetFilters = (
    <>
      {FACETS.map(({ column, title, value }) => (
        <DataTableFacetedFilter
          key={column}
          column={table.getColumn(column)}
          title={title}
          options={[...new Set(rows.map(value))]
            .sort()
            .map((option) => ({ label: option, value: option }))}
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
    </>
  )
  const context: ChannelsRowContext = {
    workspaceId,
    chatAgents,
    canUpdate,
    managingBotId,
    onManage,
  }

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

  let body: ReactNode
  if (groupBy === "bot") {
    body = groupByBot(bots, shown)
      .filter((group) => !filtering || group.rows.length > 0)
      .map(({ bot, rows: groupRows }) => (
        <BotGroup
          key={bot.id}
          bot={bot}
          rows={groupRows}
          allRows={rows.filter((row) => row.bot.id === bot.id)}
          defaultAgentName={agentName(bot.agent_preset_id)}
          open={!collapsed.has(`bot:${bot.id}`)}
          onToggle={() => toggle(`bot:${bot.id}`)}
          filtering={filtering}
          loading={routesLoading}
          unavailable={routesUnavailable}
          listingFailure={listingFailures.get(bot.id) ?? null}
          onAddChannel={() => setAddingTo(bot)}
          context={context}
        />
      ))
  } else if (groupBy === "agent") {
    body = groupByAgent(shown).map((group) => (
      <AgentGroup
        key={group.agentId}
        agentName={group.agentName}
        rows={group.rows}
        open={!collapsed.has(`agent:${group.agentId}`)}
        onToggle={() => toggle(`agent:${group.agentId}`)}
        context={context}
      />
    ))
  } else {
    body = shown.map((row) => (
      <ChannelRow key={row.key} row={row} groupBy="none" context={context} />
    ))
  }

  return (
    <div className="space-y-3">
      <div
        ref={toolbarRef}
        className="relative flex flex-wrap items-center justify-between gap-2"
      >
        {/* Clipped copy of the facets, measured to decide whether they fit. */}
        <div
          aria-hidden
          className="invisible absolute inset-x-0 top-0 h-0 overflow-hidden"
        >
          <div ref={facetsRef} className="flex w-max items-center gap-2">
            {facetFilters}
          </div>
        </div>
        {/* Room for the search box and Filters; the trailing controls wrap below that. */}
        <div className="flex min-w-0 flex-1 basis-[280px] items-center gap-2">
          <Input
            type="search"
            placeholder="Search channels..."
            aria-label="Search channels"
            value={search}
            onChange={(event) =>
              table.getColumn("channel")?.setFilterValue(event.target.value)
            }
            style={{ minWidth: SEARCH_MIN_WIDTH }}
            className="h-8 w-[250px] shrink text-xs"
          />
          {compact ? (
            <FiltersMenu
              active={columnFilters.filter((f) => f.id !== "channel").length}
            >
              {facetFilters}
            </FiltersMenu>
          ) : (
            facetFilters
          )}
        </div>
        <div ref={trailingRef} className="flex shrink-0 items-center gap-2">
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {channelCount(shown.filter((row) => row.kind !== "default").length)}
          </span>
          <DisplayMenu groupBy={groupBy} onChange={setGroupBy} />
        </div>
      </div>
      <div className="rounded-md border">
        <Table className="table-fixed text-xs" aria-label="Channels">
          <TableHeader>
            <HeaderRow table={table} groupBy={groupBy} />
          </TableHeader>
          <TableBody>
            {body}
            {filtering && shown.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columnSpan(groupBy)}
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
          title={channelBotTitle(addingTo)}
          routedIds={
            new Set(
              rows.flatMap((row) =>
                row.bot.id === addingTo.id && row.kind === "routed"
                  ? [row.channel.provider_channel_id]
                  : []
              )
            )
          }
          agents={chatAgents}
          onClose={() => setAddingTo(null)}
        />
      ) : null}
    </div>
  )
}

/**
 * Whether the facets must fold into one Filters button to keep the toolbar on
 * one row: the search box at its minimum, the facets and the trailing controls.
 */
function useFacetsOverflow(
  toolbarRef: RefObject<HTMLDivElement | null>,
  facetsRef: RefObject<HTMLDivElement | null>,
  trailingRef: RefObject<HTMLDivElement | null>
): boolean {
  const [compact, setCompact] = useState(false)
  useLayoutEffect(() => {
    const toolbar = toolbarRef.current
    const facets = facetsRef.current
    const trailing = trailingRef.current
    if (!toolbar || !facets || !trailing) {
      return
    }
    // Search to facets, and facets to the trailing controls.
    const gaps = 2 * 8
    const measure = () =>
      setCompact(
        SEARCH_MIN_WIDTH + facets.offsetWidth + trailing.offsetWidth + gaps >
          toolbar.clientWidth
      )
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(toolbar)
    observer.observe(facets)
    observer.observe(trailing)
    return () => observer.disconnect()
  }, [toolbarRef, facetsRef, trailingRef])
  return compact
}

/** The facets behind one dashed button, when the toolbar has no room for them. */
function FiltersMenu({
  active,
  children,
}: {
  active: number
  children: ReactNode
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 shrink-0 border-dashed"
        >
          <PlusCircledIcon className="mr-2 size-4" />
          Filters
          {active > 0 ? (
            <span className="ml-2 rounded-sm bg-secondary px-1 font-normal">
              {active}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="flex w-auto flex-col items-start gap-2 p-2"
      >
        {children}
      </PopoverContent>
    </Popover>
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

/** Sortable headers for the grouping's columns. */
function HeaderRow({
  table,
  groupBy,
}: {
  table: TanstackTable<ChannelTableRow>
  groupBy: ChannelsGroupBy
}) {
  const flat = groupBy === "none"
  const widths: Partial<Record<ChannelsColumn, string>> = {
    channel: flat ? "w-[32%]" : "w-[38%]",
    agent: flat ? "w-[20%]" : "w-[24%]",
    bot: flat ? "w-[20%]" : "w-[24%]",
  }
  return (
    <TableRow>
      {CHANNELS_LAYOUT[groupBy].map((id) => {
        const column = table.getColumn(id)
        return (
          <TableHead key={id} className={cn("h-8 text-xs", widths[id])}>
            {column ? (
              <DataTableColumnHeader
                column={column}
                title={COLUMN_TITLES[id]}
              />
            ) : null}
          </TableHead>
        )
      })}
      <TableHead className="h-8 w-[90px]" />
    </TableRow>
  )
}
