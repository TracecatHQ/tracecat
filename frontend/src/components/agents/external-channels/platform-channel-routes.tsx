"use client"

import { Plus, Trash2 } from "lucide-react"
import { type FormEvent, type ReactNode, useId, useState } from "react"
import type {
  AgentChannelBotRead,
  AgentChannelRouteRead,
  AgentPresetReadMinimal,
  ChannelBotChannel,
  ChannelBotProvider,
} from "@/client"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  type ChannelConventions,
  channelConventions,
  channelName,
  isChannelListingNeedsReinstall,
  isChannelListingNotSupported,
  routeChannelLabel,
} from "@/components/agents/external-channels/channel-bot-utils"
import {
  ChannelIcon,
  ChannelPicker,
} from "@/components/agents/external-channels/channel-picker"
import { CopyButton } from "@/components/copy-button"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  useAddChannelBotRoute,
  useChannelBotChannels,
  useChannelBotRoutableChannels,
  useChannelBotRoutes,
  useChannelBotsBusy,
  useDeleteChannelBotRoute,
  useSetChannelBotRoute,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { cn } from "@/lib/utils"

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

type RouteMembership = "member" | "not_member" | "unknown"

/** Whether the bot is in a routed channel, as far as the loaded listing tells. */
function routeMembership(
  known: ChannelBotChannel | undefined,
  listing: {
    channels: ChannelBotChannel[] | undefined
    hasMore: boolean
    error: TracecatApiError | null
  }
): RouteMembership {
  if (known) {
    return known.is_member ? "member" : "not_member"
  }
  // Unlisted once every page is loaded means private without the bot.
  if (listing.channels && !listing.hasMore && !listing.error) {
    return "not_member"
  }
  return "unknown"
}

/** Why the channels cannot be picked, or null while the picker works. */
function listingFailure(
  providerName: string,
  running: boolean,
  channels: ChannelBotChannel[] | undefined,
  error: unknown
): { message: string; canRetry: boolean } | null {
  // The API lists channels only for a running install.
  if (!running) {
    return {
      message: `Channel names load while Tracecat for ${providerName} is active.`,
      canRetry: false,
    }
  }
  // A failed first page falls back to typing the ID; a failed later page keeps the picker.
  if (!error || channels) {
    return null
  }
  if (isChannelListingNotSupported(error)) {
    return {
      message: `${providerName} channels can't be listed here.`,
      canRetry: false,
    }
  }
  if (isChannelListingNeedsReinstall(error)) {
    return {
      message: `Reconnect Tracecat for ${providerName} to load channel names.`,
      canRetry: false,
    }
  }
  return {
    message: `Couldn't load channel names from ${providerName}.`,
    canRetry: true,
  }
}

/** One routed channel, its agent, and a hint when the bot is not in it yet. */
function RouteRow({
  route,
  known,
  membership,
  provider,
  conventions,
  agents,
  disabled,
  onChangeAgent,
  onRemove,
}: {
  route: AgentChannelRouteRead
  known: ChannelBotChannel | undefined
  membership: RouteMembership
  provider: ChannelBotProvider
  conventions: ChannelConventions
  agents: AgentPresetReadMinimal[]
  disabled: boolean
  onChangeAgent: (agentId: string) => void
  onRemove: () => void
}) {
  const providerName = CHANNEL_BOT_PROVIDER_NAMES[provider]
  const { inviteCommand } = conventions
  const label = routeChannelLabel(provider, route.provider_channel_id, known)
  return (
    <li className="space-y-1">
      <div className="flex items-center gap-2">
        <ChannelIcon channel={known} conventions={conventions} />
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-xs",
            !known && "font-mono"
          )}
        >
          {known ? channelName(known) : route.provider_channel_id}
        </span>
        <AgentSelect
          label={`Agent for ${label}`}
          value={route.agent_preset_id}
          agents={agents}
          disabled={disabled}
          onChange={onChangeAgent}
        />
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-8"
          aria-label={`Remove ${label}`}
          disabled={disabled}
          onClick={onRemove}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
      {membership === "not_member" && (
        <p className="flex items-center gap-2 pl-[22px] text-xs text-muted-foreground">
          {inviteCommand ? (
            <>
              <span>
                Not in this channel yet. Run <code>{inviteCommand}</code> there.
              </span>
              <CopyButton
                value={inviteCommand}
                toastMessage="Invite command copied"
                tooltipMessage={`Copy ${inviteCommand}`}
              />
            </>
          ) : (
            `Not in this channel yet. Add Tracecat in ${providerName} to start answering there.`
          )}
        </p>
      )}
    </li>
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
  const running = install.status === "active"
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
    retryChannels,
  } = useChannelBotRoutableChannels(workspaceId, install.id, {
    enabled: running,
  })
  // The bot's own channels name private ones the workspace listing hasn't reached.
  const { channels: botChannels } = useChannelBotChannels(
    workspaceId,
    install.id,
    { enabled: running }
  )
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
  const conventions = channelConventions(install.provider)

  const newChannel = conventions.normalizeId(channel)
  // A hint over the loaded pages; the API refuses a channel that already has a route.
  const routedIds = new Set((routes ?? []).map((r) => r.provider_channel_id))
  const taken = routedIds.has(newChannel)
  const channelsById = new Map(
    [...(channels ?? []), ...(botChannels ?? [])].map((c) => [c.id, c])
  )
  const pickable = channels ? [...channelsById.values()] : []
  const unrouted = pickable.filter((c) => !routedIds.has(c.id))
  const failure = listingFailure(providerName, running, channels, channelsError)
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
      <ul className="space-y-2" aria-label="Channel agents">
        {routes.map((r) => {
          const known = channelsById.get(r.provider_channel_id)
          return (
            <RouteRow
              key={r.provider_channel_id}
              route={r}
              known={known}
              membership={routeMembership(known, {
                channels,
                hasMore: hasMoreChannels,
                error: channelsError,
              })}
              provider={install.provider}
              conventions={conventions}
              agents={agents}
              disabled={disabled}
              onChangeAgent={(agentId) =>
                changeRoute(r.provider_channel_id, agentId)
              }
              onRemove={() =>
                deleteRoute({
                  botId: install.id,
                  providerChannelId: r.provider_channel_id,
                })
              }
            />
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
            Answers DMs and any channel without its own agent.
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
        <div>
          <p className="text-xs font-medium">Channel agents</p>
          <p className="text-xs text-muted-foreground">
            Give a channel its own agent instead of the default.
          </p>
        </div>
        {failure ? (
          <p className="flex flex-wrap items-center gap-x-2 text-xs">
            {failure.message}
            {failure.canRetry ? (
              <Button
                type="button"
                size="sm"
                variant="link"
                className="h-auto px-0 text-xs"
                onClick={retryChannels}
              >
                Try again
              </Button>
            ) : null}
          </p>
        ) : null}
        {routeList}
        {routesErrorLine}
        <form className="flex flex-wrap items-end gap-2" onSubmit={addRoute}>
          <div className="min-w-40 flex-1 space-y-1">
            <Label htmlFor={channelInputId} className="text-xs">
              {failure ? "Channel ID" : "Channel"}
            </Label>
            {failure ? (
              <Input
                id={channelInputId}
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                placeholder={conventions.idPlaceholder}
                className="h-8 font-mono text-xs"
                disabled={disabled}
              />
            ) : (
              <ChannelPicker
                id={channelInputId}
                conventions={conventions}
                channels={unrouted}
                routedIds={routedIds}
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
          {failure
            ? `In ${providerName}, a channel's ID is at the bottom of its details. Threads already started keep their agent.`
            : "Threads already started keep their agent."}
        </p>
      </div>
    </div>
  )
}
