"use client"

import { Info } from "lucide-react"
import { useId } from "react"
import type { AgentPresetChannelToolGroup } from "@/client"
import {
  APPROVAL_DOT,
  ApprovalButton,
  ApprovalForAll,
  LogoTile,
  RAIL_LIST_ROW,
  RailEmpty,
  ToolLogo,
} from "@/components/agents/agent-preset-rail-primitives"
import { usePresetToolFields } from "@/components/agents/agent-preset-tools-list"
import { ChannelProviderIcon } from "@/components/agents/channel-provider"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  getToolApproval,
  isInNamespaces,
  type PresetToolFields,
  setToolApproval,
  type ToolEntry,
  type ToolIndex,
} from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

const CHANNEL_GROUP_PREFIX = "channel:"

/** Drill-in key for a channel provider's tool group. */
export function channelGroupKey(provider: string): string {
  return `${CHANNEL_GROUP_PREFIX}${provider}`
}

/** Whether a drill-in key names a channel provider's tool group. */
export function isChannelGroupKey(groupKey: string): boolean {
  return groupKey.startsWith(CHANNEL_GROUP_PREFIX)
}

/** Drill-in title for a channel provider's tool group. */
export function channelGroupTitle(group: AgentPresetChannelToolGroup): string {
  return `${group.title} · ${group.label}`
}

/** The channel group whose connection replaces a registry namespace, if any. */
export function supersededBy(
  namespace: string,
  groups: AgentPresetChannelToolGroup[]
): AgentPresetChannelToolGroup | undefined {
  return groups.find((group) =>
    isInNamespaces(namespace, group.supersedes ?? [])
  )
}

/** A registry group a channel connection replaces: kept as selected, greyed, explained. */
export function SupersededGroupRow({
  group,
  replacedBy,
  onOpen,
}: {
  group: {
    icon: ToolEntry
    title: string
    namespace: string
    selected: ToolEntry[]
    entries: ToolEntry[]
  }
  replacedBy: AgentPresetChannelToolGroup
  onOpen: () => void
}) {
  const reasonId = useId()
  const reason = `Not used while this agent is connected to ${replacedBy.title}. It uses From channels instead.`
  return (
    <button
      type="button"
      aria-describedby={reasonId}
      className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}
      onClick={onOpen}
    >
      <span className="flex shrink-0 opacity-45">
        <ToolLogo entry={group.icon} />
      </span>
      <span className="shrink-0 whitespace-nowrap text-[13.5px] opacity-45">
        {group.title}
      </span>
      <span className="min-w-0 truncate font-mono text-[11.5px] text-muted-foreground/70 opacity-45">
        {group.namespace}
      </span>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-hidden
            data-testid="superseded-info"
            className="flex size-[18px] shrink-0 items-center justify-center text-muted-foreground"
          >
            <Info className="size-3.5" />
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-64">{reason}</TooltipContent>
      </Tooltip>
      <span id={reasonId} aria-hidden className="sr-only">
        {reason}
      </span>
      <span className="flex-1" />
      <span className="min-w-12 whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground opacity-45">
        {group.selected.length} of {group.entries.length}
      </span>
    </button>
  )
}

/** "From channels": one row per connected provider, opening its drill-in. */
export function ChannelToolsSection({
  channelTools,
  toolApprovals,
  index,
  onOpenGroup,
}: {
  channelTools: AgentPresetChannelToolGroup[]
  toolApprovals: PresetToolFields["toolApprovals"]
  index: ToolIndex
  onOpenGroup: (groupKey: string, title: string) => void
}) {
  return (
    <div className="pt-1">
      <p className="px-2 pb-0.5 text-xs text-muted-foreground/70">
        From channels
      </p>
      {channelTools.map((group) => {
        const asks = group.tools.filter((tool) =>
          getToolApproval(toolApprovals, tool.name, index)
        ).length
        return (
          <button
            key={group.provider}
            type="button"
            className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}
            onClick={() =>
              onOpenGroup(
                channelGroupKey(group.provider),
                channelGroupTitle(group)
              )
            }
          >
            <LogoTile>
              <ChannelProviderIcon
                provider={group.provider}
                className="size-3"
              />
            </LogoTile>
            <span className="shrink-0 whitespace-nowrap text-[13.5px]">
              {group.title}
            </span>
            <span className="min-w-0 truncate font-mono text-[11.5px] text-muted-foreground/70">
              {group.label}
            </span>
            <span className="flex-1" />
            {asks > 0 ? (
              <span
                className={APPROVAL_DOT}
                title={`${asks} ${asks === 1 ? "needs" : "need"} approval`}
              />
            ) : null}
            <span className="min-w-12 whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground">
              {group.tools.length} {group.tools.length === 1 ? "tool" : "tools"}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** Drill-in for a channel connection named by `groupKey`. */
export function ChannelToolGroupDetail({
  groupKey,
  channelTools,
  index,
  isSaving,
}: {
  groupKey: string
  channelTools: AgentPresetChannelToolGroup[]
  index: ToolIndex
  isSaving: boolean
}) {
  const group = channelTools.find(
    (candidate) => channelGroupKey(candidate.provider) === groupKey
  )
  return group ? (
    <ChannelToolList group={group} index={index} isSaving={isSaving} />
  ) : (
    <RailEmpty>Not connected to a channel.</RailEmpty>
  )
}

/** A channel connection's tools: granted, never added or removed; Ask or Auto each. */
function ChannelToolList({
  group,
  index,
  isSaving,
}: {
  group: AgentPresetChannelToolGroup
  index: ToolIndex
  isSaving: boolean
}) {
  const { value, onChange } = usePresetToolFields()
  const rows = group.tools.map((tool) => ({
    tool,
    ask: getToolApproval(value.toolApprovals, tool.name, index),
  }))

  function setApprovals(keys: string[], ask: boolean) {
    let rules = value.toolApprovals
    for (const key of keys) {
      rules = setToolApproval(rules, key, ask, index)
    }
    if (rules !== value.toolApprovals)
      onChange({ ...value, toolApprovals: rules })
  }

  return (
    <div>
      <div className="mb-2 flex items-center gap-2.5 px-2 text-[13px]">
        <LogoTile>
          <ChannelProviderIcon provider={group.provider} className="size-3" />
        </LogoTile>
        <span>{group.label}</span>
        <span className="flex-1" />
        <span className="text-xs text-muted-foreground">From channels</span>
      </div>
      <p className="px-2 pb-2 text-xs text-muted-foreground">
        Only in conversations started in {group.title}.
      </p>
      <div className="mb-1 flex h-8 items-center gap-2 border-b border-border/60 pr-0.5 pl-2">
        <span className="flex-1 text-xs text-muted-foreground">
          {rows.length} {rows.length === 1 ? "tool" : "tools"}
        </span>
        <ApprovalForAll
          allAsk={rows.length > 0 && rows.every((row) => row.ask)}
          allAuto={rows.length > 0 && rows.every((row) => !row.ask)}
          disabled={isSaving}
          onSet={(ask) =>
            setApprovals(
              rows.map((row) => row.tool.name),
              ask
            )
          }
        />
      </div>
      {rows.map(({ tool, ask }) => (
        <div key={tool.name} className={cn(RAIL_LIST_ROW, "h-9 py-0")}>
          <span className="min-w-0 shrink truncate" title={tool.description}>
            {tool.title}
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/70">
            {tool.name}
          </span>
          <ApprovalButton
            tool={tool.name}
            ask={ask}
            disabled={isSaving}
            onToggle={() => setApprovals([tool.name], !ask)}
          />
        </div>
      ))}
    </div>
  )
}
