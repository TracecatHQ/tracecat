"use client"

import { ChevronsUpDown, Hash, Lock, Plus, Trash2 } from "lucide-react"
import { type FormEvent, type ReactNode, useId, useState } from "react"
import type {
  AgentChannelBotRead,
  AgentPresetReadMinimal,
  ChannelBotChannel,
} from "@/client"
import { CHANNEL_BOT_PROVIDER_NAMES } from "@/components/agents/external-channels/channel-bot-utils"
import { CopyButton } from "@/components/copy-button"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  isChannelListingNotSupported,
  useAddChannelBotRoute,
  useChannelBotChannels,
  useChannelBotRoutes,
  useChannelBotsBusy,
  useDeleteChannelBotRoute,
  useSetChannelBotRoute,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

const INVITE_COMMAND = "/invite @Tracecat"

/** Picks one of the workspace's agents. */
export function AgentSelect({
  label,
  value,
  agents,
  disabled,
  onChange,
}: {
  label: string
  value: string
  agents: AgentPresetReadMinimal[]
  disabled: boolean
  onChange: (agentPresetId: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label} className="h-8 w-48 text-xs">
        <SelectValue placeholder="Choose an agent" />
      </SelectTrigger>
      <SelectContent>
        {agents.map((agent) => (
          <SelectItem key={agent.id} value={agent.id} className="text-xs">
            {agent.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** A lock on a private channel, else a hash. */
function ChannelIcon({ channel }: { channel?: ChannelBotChannel }) {
  return channel?.is_private ? (
    <Lock
      role="img"
      aria-label="Private channel"
      className="size-3.5 shrink-0 text-muted-foreground"
    />
  ) : (
    <Hash className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
  )
}

/** Picks one of the provider tenant's channels by name. */
function ChannelPicker({
  id,
  channels,
  value,
  isLoading,
  disabled,
  onChange,
  more,
}: {
  id: string
  channels: ChannelBotChannel[]
  value: string
  isLoading: boolean
  disabled: boolean
  onChange: (channelId: string) => void
  more: {
    hasMore: boolean
    loading: boolean
    failed: boolean
    load: () => void
  }
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const selected = channels.find((c) => c.id === value)
  // Slack lists only private channels the bot is in, so others are routed by ID.
  const typedId = search.trim().toUpperCase()
  const offerTypedId =
    /^[CG][A-Z0-9]{6,}$/.test(typedId) &&
    !channels.some((c) => c.id === typedId)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled || isLoading}
          className="h-8 w-full justify-start gap-2 px-2.5 text-xs font-normal shadow-none"
        >
          <ChannelIcon channel={selected} />
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-left",
              !selected && value && "font-mono"
            )}
          >
            {selected?.name ||
              value ||
              (isLoading ? "Loading channels…" : "Pick a channel")}
          </span>
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[--radix-popover-trigger-width] min-w-64 p-0"
      >
        <Command>
          <CommandInput
            placeholder="Search channels or paste a channel ID"
            className="h-8 text-xs"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList className="max-h-64">
            <CommandEmpty className="py-3 text-center text-xs text-muted-foreground">
              No channels found. Paste a channel ID to route a private channel.
            </CommandEmpty>
            {offerTypedId && (
              <CommandItem
                value={`use ${typedId}`}
                onSelect={() => {
                  onChange(typedId)
                  setSearch("")
                  setOpen(false)
                }}
                className="gap-2 text-xs"
              >
                <Lock className="size-3.5 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">
                  Use channel ID <span className="font-mono">{typedId}</span>
                </span>
              </CommandItem>
            )}
            {channels.map((c) => (
              <CommandItem
                key={c.id}
                value={`${c.name} ${c.id}`}
                onSelect={() => {
                  onChange(c.id)
                  setOpen(false)
                }}
                className="gap-2 text-xs"
              >
                <ChannelIcon channel={c} />
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                {!c.is_member && (
                  <span className="text-[11px] text-muted-foreground">
                    {c.is_private ? "needs invite" : "joins on add"}
                  </span>
                )}
              </CommandItem>
            ))}
            {more.hasMore && (
              // Search covers loaded pages only, so this stays while searching.
              <CommandItem
                forceMount
                value="load more channels"
                disabled={more.loading}
                onSelect={more.load}
                className="text-xs text-muted-foreground"
              >
                {more.loading
                  ? "Loading…"
                  : more.failed
                    ? "Could not load more channels. Try again"
                    : "Load more channels"}
              </CommandItem>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/**
 * A platform install's default agent, and the channels whose new threads go
 * to another agent. Existing threads keep the agent they started with.
 */
export function PlatformChannelRoutes({
  workspaceId,
  install,
  canUpdate,
}: {
  workspaceId: string
  install: AgentChannelBotRead
  canUpdate: boolean
}) {
  const { presets } = useAgentPresets(workspaceId)
  const {
    routes,
    routesIsLoading,
    routesError,
    hasMoreRoutes,
    loadingMoreRoutes,
    loadMoreRoutes,
  } = useChannelBotRoutes(workspaceId, install.id)
  const { addRoute: sendAddRoute } = useAddChannelBotRoute(
    workspaceId,
    install.provider
  )
  const {
    channels,
    channelsIsLoading,
    channelsError,
    hasMoreChannels,
    loadingMoreChannels,
    loadMoreChannels,
  } = useChannelBotChannels(workspaceId, install.id)
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, install.provider)
  const { setRoute } = useSetChannelBotRoute(workspaceId, install.provider)
  const { deleteRoute } = useDeleteChannelBotRoute(
    workspaceId,
    install.provider
  )
  const disabled = useChannelBotsBusy(workspaceId) || !canUpdate
  const [channel, setChannel] = useState("")
  const [agentPresetId, setAgentPresetId] = useState("")
  const channelInputId = useId()
  const agents = presets ?? []
  const providerName = CHANNEL_BOT_PROVIDER_NAMES[install.provider]

  // Slack channel IDs are uppercase; events never match a lowercase one.
  const newChannel =
    install.provider === "slack" ? channel.trim().toUpperCase() : channel.trim()
  // A hint over the loaded pages; the API refuses a channel that already has a route.
  const taken = routes?.some((r) => r.provider_channel_id === newChannel)
  const channelsById = new Map((channels ?? []).map((c) => [c.id, c]))
  const unrouted = (channels ?? []).filter(
    (c) => !routes?.some((r) => r.provider_channel_id === c.id)
  )
  // A failed first page falls back to typing the ID; a failed later page keeps the picker.
  const listingNote =
    channelsError && !channels
      ? isChannelListingNotSupported(channelsError)
        ? `${providerName} channels can't be listed here.`
        : "Could not load channels."
      : null
  // Channel IDs carry no whitespace; the API refuses them otherwise.
  const canAdd =
    newChannel !== "" && !/\s/.test(newChannel) && !taken && !!agentPresetId

  function changeRoute(providerChannelId: string, agentId: string): void {
    setRoute({
      botId: install.id,
      requestBody: {
        provider_channel_id: providerChannelId,
        agent_preset_id: agentId,
      },
    })
  }

  function addRoute(event: FormEvent): void {
    event.preventDefault()
    if (canAdd) {
      sendAddRoute(
        {
          botId: install.id,
          requestBody: {
            provider_channel_id: newChannel,
            agent_preset_id: agentPresetId,
          },
        },
        {
          onSuccess: () => {
            setChannel("")
            setAgentPresetId("")
          },
        }
      )
    }
  }

  function changeDefault(agentId: string): void {
    updateBot(
      { botId: install.id, requestBody: { agent_preset_id: agentId } },
      {
        onSuccess: () =>
          toast({ title: `Default ${providerName} agent changed` }),
      }
    )
  }

  let routeList: ReactNode = null
  if (routesIsLoading) {
    routeList = (
      <p className="text-xs text-muted-foreground">Loading channels...</p>
    )
  } else if (routes && routes.length > 0) {
    routeList = (
      <ul className="space-y-2" aria-label="Channels">
        {routes.map((r) => {
          const known = channelsById.get(r.provider_channel_id)
          const label = known ? `#${known.name}` : r.provider_channel_id
          return (
            <li key={r.provider_channel_id} className="space-y-1">
              <div className="flex items-center gap-2">
                <ChannelIcon channel={known} />
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate text-xs",
                    !known && "font-mono"
                  )}
                >
                  {known?.name ?? r.provider_channel_id}
                </span>
                <AgentSelect
                  label={`Agent for ${label}`}
                  value={r.agent_preset_id}
                  agents={agents}
                  disabled={disabled}
                  onChange={(agentId) =>
                    changeRoute(r.provider_channel_id, agentId)
                  }
                />
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="size-8"
                  aria-label={`Remove ${label}`}
                  disabled={disabled}
                  onClick={() =>
                    deleteRoute({
                      botId: install.id,
                      providerChannelId: r.provider_channel_id,
                    })
                  }
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
              {/* Unlisted once every page is loaded means private without the bot. */}
              {((known?.is_private && !known.is_member) ||
                (!known && channels && !hasMoreChannels && !channelsError)) && (
                <p className="flex items-center gap-2 pl-[22px] text-xs text-muted-foreground">
                  {known
                    ? `Invite @Tracecat in #${known.name} to finish`
                    : "Invite @Tracecat in this channel to finish"}
                  <CopyButton
                    value={INVITE_COMMAND}
                    toastMessage="Invite command copied"
                    tooltipMessage={`Copy ${INVITE_COMMAND}`}
                  />
                </p>
              )}
            </li>
          )
        })}
        {hasMoreRoutes ? (
          <li>
            <Button
              type="button"
              size="sm"
              variant="link"
              className="h-auto px-0 text-xs"
              disabled={loadingMoreRoutes}
              onClick={loadMoreRoutes}
            >
              {loadingMoreRoutes ? "Loading..." : "Show more"}
            </Button>
          </li>
        ) : null}
      </ul>
    )
  } else if (!routesError) {
    routeList = (
      <p className="text-xs text-muted-foreground">
        Every channel uses the default agent.
      </p>
    )
  }
  // A failed later page keeps the pages already loaded, and Show more retries.
  const routesErrorLine = routesError ? (
    <p className="text-xs text-destructive">
      {getApiErrorDetail(routesError) ?? "Failed to load the channels."}
    </p>
  ) : null

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="min-w-48 flex-1">
          <p className="text-xs font-medium">Default agent</p>
          <p className="text-xs text-muted-foreground">
            Answers DMs and any channel not listed below.
          </p>
        </div>
        <AgentSelect
          label="Default agent"
          value={install.agent_preset_id}
          agents={agents}
          disabled={disabled}
          onChange={changeDefault}
        />
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium">Channels</p>
        {routeList}
        {routesErrorLine}
        <form className="flex flex-wrap items-end gap-2" onSubmit={addRoute}>
          <div className="min-w-40 flex-1 space-y-1">
            <Label htmlFor={channelInputId} className="text-xs">
              {listingNote ? "Channel ID" : "Channel"}
            </Label>
            {listingNote ? (
              <Input
                id={channelInputId}
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                placeholder="C0123456789"
                className="h-8 font-mono text-xs"
                disabled={disabled}
              />
            ) : (
              <ChannelPicker
                id={channelInputId}
                channels={unrouted}
                value={channel}
                isLoading={channelsIsLoading}
                disabled={disabled}
                onChange={setChannel}
                more={{
                  hasMore: hasMoreChannels,
                  loading: loadingMoreChannels,
                  failed: !!channelsError,
                  load: loadMoreChannels,
                }}
              />
            )}
          </div>
          <AgentSelect
            label="Agent for the new route"
            value={agentPresetId}
            agents={agents}
            disabled={disabled}
            onChange={setAgentPresetId}
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={disabled || !canAdd}
          >
            <Plus className="mr-2 size-4" />
            Add
          </Button>
        </form>
        <p className="text-xs text-muted-foreground">
          {listingNote
            ? `${listingNote} Copy a channel's ID from its details in ${providerName}. Threads already started keep their agent.`
            : "Threads already started keep their agent."}
        </p>
      </div>
    </div>
  )
}
