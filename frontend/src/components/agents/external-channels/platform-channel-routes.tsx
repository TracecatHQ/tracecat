"use client"

import { Hash, Plus, Trash2 } from "lucide-react"
import { type FormEvent, type ReactNode, useId, useState } from "react"
import type { AgentChannelBotRead, AgentPresetReadMinimal } from "@/client"
import { CHANNEL_BOT_PROVIDER_NAMES } from "@/components/agents/external-channels/channel-bot-utils"
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
  useChannelBotRoutes,
  useChannelBotsBusy,
  useDeleteChannelBotRoute,
  useSetChannelBotRoute,
  useUpdateAgentChannelBot,
} from "@/hooks/use-agent-channel-bots"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { getApiErrorDetail } from "@/lib/errors"

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
  const { routes, routesIsLoading, routesError } = useChannelBotRoutes(
    workspaceId,
    install.id
  )
  const { updateBot } = useUpdateAgentChannelBot(workspaceId, install.provider)
  const { setRoute } = useSetChannelBotRoute(workspaceId, install.provider)
  const { deleteRoute } = useDeleteChannelBotRoute(
    workspaceId,
    install.provider
  )
  const disabled = useChannelBotsBusy(workspaceId) || !canUpdate
  // An unloaded list hides the routes an add would overwrite.
  const routesDisabled = disabled || !routes
  const [channel, setChannel] = useState("")
  const [agentPresetId, setAgentPresetId] = useState("")
  const channelInputId = useId()
  const agents = presets ?? []
  const providerName = CHANNEL_BOT_PROVIDER_NAMES[install.provider]

  // Slack channel IDs are uppercase; events never match a lowercase one.
  const newChannel =
    install.provider === "slack" ? channel.trim().toUpperCase() : channel.trim()
  const taken = routes?.some((r) => r.provider_channel_id === newChannel)
  // Channel IDs carry no whitespace; the API refuses them otherwise.
  const canAdd =
    newChannel !== "" && !/\s/.test(newChannel) && !taken && !!agentPresetId

  function route(
    providerChannelId: string,
    agentId: string,
    onSuccess?: () => void
  ): void {
    setRoute(
      {
        botId: install.id,
        requestBody: {
          provider_channel_id: providerChannelId,
          agent_preset_id: agentId,
        },
      },
      { onSuccess }
    )
  }

  function addRoute(event: FormEvent): void {
    event.preventDefault()
    if (canAdd) {
      route(newChannel, agentPresetId, () => {
        setChannel("")
        setAgentPresetId("")
      })
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

  let routeList: ReactNode
  if (routesError) {
    routeList = (
      <p className="text-xs text-destructive">
        {getApiErrorDetail(routesError) ?? "Failed to load the channels."}
      </p>
    )
  } else if (routesIsLoading) {
    routeList = (
      <p className="text-xs text-muted-foreground">Loading channels...</p>
    )
  } else if (routes && routes.length > 0) {
    routeList = (
      <ul className="space-y-2" aria-label="Channels">
        {routes.map((r) => (
          <li key={r.provider_channel_id} className="flex items-center gap-2">
            <Hash
              className="size-3.5 shrink-0 text-muted-foreground"
              aria-hidden
            />
            <span className="min-w-0 flex-1 truncate font-mono text-xs">
              {r.provider_channel_id}
            </span>
            <AgentSelect
              label={`Agent for ${r.provider_channel_id}`}
              value={r.agent_preset_id}
              agents={agents}
              disabled={disabled}
              onChange={(agentId) => route(r.provider_channel_id, agentId)}
            />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-8"
              aria-label={`Remove ${r.provider_channel_id}`}
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
          </li>
        ))}
      </ul>
    )
  } else {
    routeList = (
      <p className="text-xs text-muted-foreground">
        Every channel uses the default agent.
      </p>
    )
  }

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
        <form className="flex flex-wrap items-end gap-2" onSubmit={addRoute}>
          <div className="min-w-40 flex-1 space-y-1">
            <Label htmlFor={channelInputId} className="text-xs">
              Channel ID
            </Label>
            <Input
              id={channelInputId}
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              placeholder="C0123456789"
              className="h-8 font-mono text-xs"
              disabled={routesDisabled}
            />
          </div>
          <AgentSelect
            label="Agent for the new route"
            value={agentPresetId}
            agents={agents}
            disabled={routesDisabled}
            onChange={setAgentPresetId}
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={routesDisabled || !canAdd}
          >
            <Plus className="mr-2 size-4" />
            Add
          </Button>
        </form>
        <p className="text-xs text-muted-foreground">
          {`Copy a channel's ID from its details in ${providerName}. Threads already started keep their agent.`}
        </p>
      </div>
    </div>
  )
}
