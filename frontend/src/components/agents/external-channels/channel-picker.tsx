"use client"

import { ChevronsUpDown, Hash, Lock } from "lucide-react"
import { useState } from "react"
import type { ChannelBotChannel } from "@/client"
import {
  type ChannelConventions,
  channelName,
} from "@/components/agents/external-channels/channel-bot-utils"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/** A lock on a private channel, else the provider's public prefix. */
export function ChannelIcon({
  channel,
  conventions,
}: {
  channel?: ChannelBotChannel
  conventions: ChannelConventions
}) {
  if (channel?.is_private) {
    return (
      <Lock
        role="img"
        aria-label="Private channel"
        className="size-3.5 shrink-0 text-muted-foreground"
      />
    )
  }
  return conventions.publicPrefix === "#" ? (
    <Hash className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
  ) : (
    <span className="size-3.5 shrink-0" aria-hidden />
  )
}

/** A note for a listed channel the bot is not in yet, or null when it is. */
function notMemberNote(channel: ChannelBotChannel): string | null {
  return channel.is_member ? null : "Tracecat not in channel yet"
}

/** Picks one of the provider tenant's channels by name. */
export function ChannelPicker({
  id,
  conventions,
  channels,
  value,
  isLoading,
  disabled,
  onChange,
  more,
}: {
  id: string
  conventions: ChannelConventions
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
  // Unlisted channels, such as Slack's private ones without the bot, are routed by ID.
  const typedId = conventions.normalizeId(search)
  const offerTypedId =
    conventions.isChannelId(typedId) && !channels.some((c) => c.id === typedId)

  function pick(channelId: string): void {
    onChange(channelId)
    setSearch("")
    setOpen(false)
  }

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
          <ChannelIcon channel={selected} conventions={conventions} />
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-left",
              !selected && value && "font-mono"
            )}
          >
            {(selected && channelName(selected)) ||
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
                onSelect={() => pick(typedId)}
                className="gap-2 text-xs"
              >
                <Lock className="size-3.5 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">
                  Use channel ID <span className="font-mono">{typedId}</span>
                </span>
              </CommandItem>
            )}
            {channels.map((c) => {
              const note = notMemberNote(c)
              return (
                <CommandItem
                  key={c.id}
                  value={`${channelName(c)} ${c.id}`}
                  onSelect={() => pick(c.id)}
                  className="gap-2 text-xs"
                >
                  <ChannelIcon channel={c} conventions={conventions} />
                  <span className="min-w-0 flex-1 truncate">
                    {channelName(c)}
                  </span>
                  {note && (
                    <span className="text-[11px] text-muted-foreground">
                      {note}
                    </span>
                  )}
                </CommandItem>
              )
            })}
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
