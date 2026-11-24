"use client"

import { HashIcon } from "lucide-react"
import { useState } from "react"
import type { ChannelDestination } from "@/client"
import { PromptInputButton } from "@/components/ai-elements/prompt-input"
import { SlackIcon } from "@/components/icons"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Kbd } from "@/components/ui/kbd"
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useSessionChannelDestinations,
  useSessionChannelSync,
} from "@/hooks/use-session-channel-sync"
import { cn } from "@/lib/utils"

/** How the composer's Slack button draws the session's sync state. */
export type SlackSyncState = "on" | "off" | "unlinked"

const BUTTON_LABELS: Record<SlackSyncState, string> = {
  on: "Slack sync on",
  off: "Slack sync off",
  unlinked: "Sync with Slack",
}

const TOOLTIPS: Record<SlackSyncState, string> = {
  on: "Syncing with the Slack thread",
  off: "Not syncing to Slack · click to sync",
  unlinked: "Sync with Slack · pick a channel",
}

/**
 * The composer's Slack sync toggle. A linked session turns sync on or off; an
 * unlinked one opens the channel picker. Hidden when the actor may not change
 * sync or the session's agent has no bot.
 */
export function SlackSyncButton({
  workspaceId,
  sessionId,
  disabled = false,
}: {
  workspaceId: string
  sessionId: string
  disabled?: boolean
}) {
  const { sync, changeSync, isPending } = useSessionChannelSync(
    workspaceId,
    sessionId
  )
  const [pickerOpen, setPickerOpen] = useState(false)
  if (!sync?.available) {
    return null
  }
  let state: SlackSyncState = "unlinked"
  if (sync.linked) {
    state = sync.enabled ? "on" : "off"
  }

  // Unlinked, the button is the picker's trigger, so a second click closes it.
  const PickerAnchor = state === "unlinked" ? PopoverTrigger : PopoverAnchor

  function handleClick() {
    if (state !== "unlinked") {
      changeSync({ enabled: state === "off" })
    }
  }

  const inactive = disabled || isPending

  function handleSelect(destination: ChannelDestination) {
    setPickerOpen(false)
    if (!inactive) {
      changeSync({ enabled: true, destination })
    }
  }

  return (
    // The trigger is a span, so a disabled button alone does not keep the picker shut.
    <Popover
      open={pickerOpen && !inactive}
      onOpenChange={(open) => setPickerOpen(open && !inactive)}
    >
      <PickerAnchor asChild>
        <span className="inline-flex">
          <Tooltip>
            <TooltipTrigger asChild>
              <PromptInputButton
                aria-label={BUTTON_LABELS[state]}
                aria-pressed={state === "unlinked" ? undefined : state === "on"}
                aria-expanded={state === "unlinked" ? pickerOpen : undefined}
                data-state-sync={state}
                disabled={inactive}
                onClick={handleClick}
                className={cn("h-7", pickerOpen && "bg-accent")}
              >
                <SlackSyncIcon state={state} />
              </PromptInputButton>
            </TooltipTrigger>
            <TooltipContent side="top">{TOOLTIPS[state]}</TooltipContent>
          </Tooltip>
        </span>
      </PickerAnchor>
      <PopoverContent side="top" align="start" className="w-80 p-0">
        {pickerOpen ? (
          <SlackChannelPicker
            workspaceId={workspaceId}
            sessionId={sessionId}
            onSelect={handleSelect}
          />
        ) : null}
      </PopoverContent>
    </Popover>
  )
}

/** The Slack mark: full color when syncing, grey with a strike when off, plain grey when unlinked. */
export function SlackSyncIcon({ state }: { state: SlackSyncState }) {
  return (
    <span className="relative inline-flex size-4 items-center justify-center">
      <SlackIcon
        aria-hidden="true"
        className={cn(
          "size-4",
          state !== "on" && "opacity-60 grayscale",
          state === "off" && "opacity-40"
        )}
      />
      {state === "off" ? (
        <span
          aria-hidden="true"
          className="absolute h-0.5 w-5 -rotate-45 rounded-full bg-muted-foreground ring-2 ring-background"
        />
      ) : null}
    </span>
  )
}

/** "Search in Slack…": a DM with you, recent channels, then the bot's channels. */
export function SlackChannelPicker({
  workspaceId,
  sessionId,
  onSelect,
}: {
  workspaceId: string
  sessionId: string
  onSelect: (destination: ChannelDestination) => void
}) {
  const { destinations, isLoading } = useSessionChannelDestinations(
    workspaceId,
    sessionId,
    true
  )
  const items = destinations ?? []
  const direct = items.filter((item) => item.kind === "direct_message")
  const recent = items.filter((item) => item.kind === "channel" && item.recent)
  const channels = items.filter(
    (item) => item.kind === "channel" && !item.recent
  )

  return (
    <Command loop>
      <CommandInput placeholder="Search in Slack…" className="h-9 text-sm" />
      <CommandList className="max-h-72">
        <CommandEmpty className="px-3 py-4 text-center text-xs text-muted-foreground">
          {isLoading ? "Loading channels…" : "No channels found."}
        </CommandEmpty>
        {direct.length > 0 ? (
          <CommandGroup heading="Direct messages">
            {direct.map((item) => (
              <CommandItem
                key="direct-message"
                value="direct message you"
                onSelect={() => onSelect(item)}
                className="gap-2"
              >
                <SlackIcon aria-hidden="true" className="size-4" />
                Direct message (you)
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        <DestinationGroup
          heading="Recent"
          items={recent}
          onSelect={onSelect}
          keyPrefix="recent"
        />
        <DestinationGroup
          heading="Channels"
          items={channels}
          onSelect={onSelect}
          keyPrefix="channel"
        />
      </CommandList>
      <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd>
          <span className="mr-2">Navigate</span>
          <Kbd>esc</Kbd>
          <span>Close</span>
        </span>
        <span className="flex items-center gap-1">
          <Kbd>↵</Kbd>
          <span>Select</span>
        </span>
      </div>
    </Command>
  )
}

function DestinationGroup({
  heading,
  items,
  onSelect,
  keyPrefix,
}: {
  heading: string
  items: ChannelDestination[]
  onSelect: (destination: ChannelDestination) => void
  keyPrefix: string
}) {
  if (items.length === 0) {
    return null
  }
  return (
    <CommandGroup heading={heading}>
      {items.map((item) => (
        <CommandItem
          key={`${keyPrefix}-${item.id}`}
          value={`${item.name} ${item.id}`}
          onSelect={() => onSelect(item)}
          className="gap-2"
        >
          <HashIcon
            aria-hidden="true"
            className="size-4 text-muted-foreground"
          />
          {item.name}
        </CommandItem>
      ))}
    </CommandGroup>
  )
}
