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
import { useWorkspaceChannelBots } from "@/hooks/use-agent-channel-bots"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  channelDestinationLabel,
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
 *
 * With `onPickedChange`, a session with no messages keeps its pick in the
 * caller's state; the first send carries it and the thread opens at that turn's end.
 * `openPicker` opens the picker on mount, for a draft that made its session to pick.
 */
export function SlackSyncButton({
  workspaceId,
  sessionId,
  disabled = false,
  picked = null,
  onPickedChange,
  openPicker = false,
}: {
  workspaceId: string
  sessionId: string
  disabled?: boolean
  picked?: ChannelDestination | null
  onPickedChange?: (destination: ChannelDestination | null) => void
  openPicker?: boolean
}) {
  const { sync, changeSync, isPending } = useSessionChannelSync(
    workspaceId,
    sessionId
  )
  const [pickerOpen, setPickerOpen] = useState(openPicker)
  if (!sync?.available) {
    return null
  }
  const pickFirst = !sync.linked && onPickedChange !== undefined
  const waiting = pickFirst ? picked : null
  let state: SlackSyncState = "unlinked"
  if (sync.linked) {
    state = sync.enabled ? "on" : "off"
  } else if (waiting) {
    state = "on"
  }
  const tooltip = waiting
    ? `Syncs to ${channelDestinationLabel(waiting)} when you send · click to cancel`
    : TOOLTIPS[state]

  // Unlinked, the button is the picker's trigger, so a second click closes it.
  const PickerAnchor = state === "unlinked" ? PopoverTrigger : PopoverAnchor

  function handleClick() {
    if (waiting) {
      onPickedChange?.(null)
    } else if (state !== "unlinked") {
      changeSync({ enabled: state === "off" })
    }
  }

  const inactive = disabled || isPending

  function handleSelect(destination: ChannelDestination) {
    setPickerOpen(false)
    if (inactive) return
    if (pickFirst) {
      onPickedChange?.(destination)
    } else {
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
            <TooltipContent side="top">{tooltip}</TooltipContent>
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
/**
 * A draft chat's Slack button, shown when the workspace has an active bot.
 * Clicking creates the session; its own button then opens the picker.
 */
export function DraftSlackSyncButton({
  workspaceId,
  disabled = false,
  onCreateSession,
}: {
  workspaceId: string
  disabled?: boolean
  onCreateSession: () => Promise<void>
}) {
  const { hasEntitlement } = useEntitlements()
  const { data: bots } = useWorkspaceChannelBots(
    workspaceId,
    hasEntitlement("agent_channels_preview")
  )
  const [creating, setCreating] = useState(false)
  if (!bots?.some((bot) => bot.status === "active")) {
    return null
  }

  async function handleClick() {
    setCreating(true)
    try {
      await onCreateSession()
    } finally {
      setCreating(false)
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <PromptInputButton
          aria-label={BUTTON_LABELS.unlinked}
          data-state-sync="unlinked"
          disabled={disabled || creating}
          onClick={() => void handleClick()}
          className="h-7"
        >
          <SlackSyncIcon state="unlinked" />
        </PromptInputButton>
      </TooltipTrigger>
      <TooltipContent side="top">{TOOLTIPS.unlinked}</TooltipContent>
    </Tooltip>
  )
}

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
