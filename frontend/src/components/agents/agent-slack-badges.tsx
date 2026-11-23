"use client"

import { Lock } from "lucide-react"
import { forwardRef } from "react"
import type { ChannelBotChannel } from "@/client"
import { channelLabel } from "@/components/agents/external-channels/channel-bot-utils"
import { OverflowTooltipList } from "@/components/agents/overflow-tooltip-list"
import { SlackIcon } from "@/components/icons"
import { Badge, type BadgeProps } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/**
 * Where an agent answers in Slack: one channel its bot is in or routes to it,
 * or, when a bot's channels cannot be listed, every channel of its own Slack
 * app or the platform install's channels without a route.
 */
export type SlackPlacement =
  | {
      kind: "workspace_bot"
      botId: string
      /** The app lacks the scopes to list its channels until reinstalled. */
      needsReinstall: boolean
    }
  | { kind: "default" }
  | { kind: "channel"; channel: ChannelBotChannel }

const SLACK_BADGE_CAP = 2
const REINSTALL_NOTE = "Reinstall to show channels"

function needsReinstall(placement: SlackPlacement): boolean {
  return placement.kind === "workspace_bot" && placement.needsReinstall
}

/** A Slack placement in words: `#name` or a private name. */
export function slackPlacementLabel(placement: SlackPlacement): string {
  switch (placement.kind) {
    case "workspace_bot":
      return "All channels"
    case "default":
      return "All other channels"
    case "channel":
      return channelLabel("slack", placement.channel)
  }
}

function slackPlacementKey(placement: SlackPlacement): string {
  switch (placement.kind) {
    case "workspace_bot":
      return `bot:${placement.botId}`
    case "default":
      return "default"
    case "channel":
      return `channel:${placement.channel.id}`
  }
}

type SlackBadgeProps = BadgeProps & { label: string; isPrivate: boolean }

const SlackBadge = forwardRef<HTMLDivElement, SlackBadgeProps>(
  ({ label, isPrivate, className, ...props }, ref) => (
    <Badge
      ref={ref}
      variant="secondary"
      className={cn(
        "h-5 max-w-[160px] gap-1 px-2 text-[10px] font-normal",
        className
      )}
      {...props}
    >
      <SlackIcon className="size-3 shrink-0" aria-label="Slack" />
      {isPrivate ? (
        <Lock className="size-3 shrink-0" aria-label="Private channel" />
      ) : null}
      <span title={label} className="truncate">
        {label}
      </span>
    </Badge>
  )
)
SlackBadge.displayName = "SlackBadge"

/** A Slack channel's badge: `#name`, or a lock and the name when private. */
export function SlackChannelBadge({
  channel,
  className,
}: {
  channel: ChannelBotChannel
  className?: string
}) {
  return (
    <SlackBadge
      label={channelLabel("slack", channel)}
      isPrivate={channel.is_private}
      className={className}
    />
  )
}

function SlackPlacementBadge({ placement }: { placement: SlackPlacement }) {
  const badge = (
    <SlackBadge
      label={slackPlacementLabel(placement)}
      isPrivate={placement.kind === "channel" && placement.channel.is_private}
    />
  )
  if (!needsReinstall(placement)) {
    return badge
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent>
        <span className="opacity-70">{REINSTALL_NOTE}</span>
      </TooltipContent>
    </Tooltip>
  )
}

/** Badges for where an agent answers in Slack, with overflow as `+N`. */
export function AgentSlackBadges({
  placements,
}: {
  placements: SlackPlacement[]
}) {
  if (placements.length === 0) return null
  const overflow = placements.slice(SLACK_BADGE_CAP)

  return (
    <div
      data-testid="slack-badges"
      className="flex shrink-0 items-center gap-1"
    >
      {placements.slice(0, SLACK_BADGE_CAP).map((placement) => (
        <SlackPlacementBadge
          key={slackPlacementKey(placement)}
          placement={placement}
        />
      ))}
      {overflow.length > 0 ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              className="h-5 cursor-default px-2 text-[10px] font-normal"
            >
              +{overflow.length}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <OverflowTooltipList
              items={overflow.map((placement) => ({
                key: slackPlacementKey(placement),
                label: slackPlacementLabel(placement),
                icon:
                  placement.kind === "channel" &&
                  placement.channel.is_private ? (
                    <Lock
                      className="size-3 shrink-0"
                      aria-label="Private channel"
                    />
                  ) : undefined,
              }))}
            />
            {overflow.some(needsReinstall) ? (
              <p className="mt-1 opacity-70">{REINSTALL_NOTE}</p>
            ) : null}
          </TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}
