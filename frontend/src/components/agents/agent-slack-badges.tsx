"use client"

import { Lock } from "lucide-react"
import { channelLabel } from "@/components/agents/external-channels/channel-bot-utils"
import { OverflowTooltipList } from "@/components/agents/overflow-tooltip-list"
import { SlackIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SlackPlacement } from "@/lib/agent-catalog-filters"

const SLACK_BADGE_CAP = 2

/** A Slack placement in words: `#name`, a private name, or the raw ID. */
export function slackPlacementLabel(placement: SlackPlacement): string {
  switch (placement.kind) {
    case "workspace_bot":
      return "All channels"
    case "default":
      return "All other channels"
    case "channel":
      if (!placement.channel) return placement.channelId
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
      return `channel:${placement.channelId}`
  }
}

function SlackPlacementText({ placement }: { placement: SlackPlacement }) {
  const label = slackPlacementLabel(placement)
  if (placement.kind === "channel" && !placement.channel) {
    return (
      <span title={label} className="truncate font-mono">
        {label}
      </span>
    )
  }
  return (
    <>
      {placement.kind === "channel" && placement.channel?.is_private ? (
        <Lock className="size-3 shrink-0" aria-label="Private channel" />
      ) : null}
      <span title={label} className="truncate">
        {label}
      </span>
    </>
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
      {placements.slice(0, SLACK_BADGE_CAP).map((placement, index) => (
        <Badge
          key={slackPlacementKey(placement)}
          variant="secondary"
          className="h-5 max-w-[160px] gap-1 px-2 text-[10px] font-normal"
        >
          {index === 0 ? (
            <SlackIcon className="size-3 shrink-0" aria-label="Slack" />
          ) : null}
          <SlackPlacementText placement={placement} />
        </Badge>
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
              }))}
            />
          </TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}
