"use client"

import { Loader2 } from "lucide-react"
import { type FormEvent, useId, useState } from "react"
import type {
  AgentChannelBotRead,
  AgentPresetReadMinimal,
  ChannelBotChannel,
} from "@/client"
import { AgentSelect } from "@/components/agents/external-channels/channel-bot-fields"
import {
  CHANNEL_BOT_PROVIDER_NAMES,
  channelConventions,
  isChannelListingNeedsReinstall,
  isChannelListingNotSupported,
} from "@/components/agents/external-channels/channel-bot-utils"
import { ChannelPicker } from "@/components/agents/external-channels/channel-picker"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  useChannelBotChannels,
  useChannelBotRoutableChannels,
  useChannelBotsBusy,
  useSetChannelBotRoute,
} from "@/hooks/use-agent-channel-bots"

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

/** Gives one more channel of a platform install its own agent. */
export function AddChannelRouteDialog({
  workspaceId,
  install,
  title,
  routedIds,
  agents,
  onClose,
}: {
  workspaceId: string
  install: AgentChannelBotRead
  title: string
  /** Channels the table already lists with their own agent. */
  routedIds: Set<string>
  agents: AgentPresetReadMinimal[]
  onClose: () => void
}) {
  const running = install.status === "active"
  // An upsert: a route to a deleted agent is hidden from the table, yet still exists.
  const { setRoute, setRouteIsPending } = useSetChannelBotRoute(
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
  const busy = useChannelBotsBusy(workspaceId)
  const [channel, setChannel] = useState("")
  const [agentPresetId, setAgentPresetId] = useState("")
  const formId = useId()
  const channelInputId = useId()
  const providerName = CHANNEL_BOT_PROVIDER_NAMES[install.provider]
  const conventions = channelConventions(install.provider)

  const newChannel = conventions.normalizeId(channel)
  const channelsById = new Map(
    [...(channels ?? []), ...(botChannels ?? [])].map((c) => [c.id, c])
  )
  const pickable = channels ? [...channelsById.values()] : []
  const unrouted = pickable.filter((c) => !routedIds.has(c.id))
  const failure = listingFailure(providerName, running, channels, channelsError)
  // A typed ID must look like one of the provider's, or the route never matches.
  const canAdd =
    conventions.isChannelId(newChannel) &&
    !routedIds.has(newChannel) &&
    !!agentPresetId

  function submit(event: FormEvent): void {
    event.preventDefault()
    if (!canAdd) {
      return
    }
    setRoute(
      {
        botId: install.id,
        requestBody: {
          provider_channel_id: newChannel,
          agent_preset_id: agentPresetId,
        },
      },
      { onSuccess: onClose }
    )
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) {
          onClose()
        }
      }}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Add channel to {title}</DialogTitle>
          <DialogDescription>
            New threads there go to this agent instead of the default. Threads
            already started keep their agent.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <form id={formId} className="space-y-4" onSubmit={submit}>
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
            <div className="space-y-1.5">
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
                  disabled={busy}
                />
              ) : (
                <ChannelPicker
                  id={channelInputId}
                  conventions={conventions}
                  channels={unrouted}
                  routedIds={routedIds}
                  value={channel}
                  isLoading={channelsIsLoading}
                  disabled={busy}
                  onChange={setChannel}
                  more={{
                    hasMore: hasMoreChannels,
                    loading: loadingMoreChannels,
                    failed: !!channelsError,
                    load: loadMoreChannels,
                  }}
                />
              )}
              {failure ? (
                <p className="text-xs text-muted-foreground">
                  In {providerName}, a channel&apos;s ID is at the bottom of its
                  details.
                </p>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Agent</p>
              <AgentSelect
                label="Agent for the new channel"
                value={agentPresetId}
                agents={agents}
                disabled={busy}
                onChange={setAgentPresetId}
                className="w-full"
              />
            </div>
          </form>
        </DialogBody>
        <DialogFooter>
          <Button
            type="submit"
            size="sm"
            form={formId}
            disabled={busy || !canAdd}
          >
            {setRouteIsPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            Add channel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
