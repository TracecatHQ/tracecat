"use client"

import { X } from "lucide-react"
import { useMemo, useState } from "react"
import { useFormContext, useWatch } from "react-hook-form"
import type {
  AgentPresetChannelToolGroup,
  MCPIntegrationRead,
  RegistryActionReadMinimal,
} from "@/client"
import {
  ChannelToolGroupDetail,
  ChannelToolsSection,
  isChannelGroupKey,
  SupersededGroupRow,
  supersededBy,
} from "@/components/agents/agent-preset-rail-channel-tools"
import {
  APPROVAL_DOT,
  APPROVAL_TEXT,
  ApprovalButton,
  ApprovalForAll,
  LogoTile,
  RAIL_FOCUS,
  RAIL_LIST_ROW,
  RAIL_REMOVE_BUTTON,
  RailEmpty,
  RailSectionHeader,
  ToolLogo,
} from "@/components/agents/agent-preset-rail-primitives"
import { usePresetToolFields } from "@/components/agents/agent-preset-tools-list"
import { ToolPickerDialog } from "@/components/tools/tool-picker-dialog"
import { TunnelSelect } from "@/components/tunnels/tunnel-select"
import { Button } from "@/components/ui/button"
import {
  buildToolIndex,
  getBlockedActions,
  getToolApproval,
  removeTools,
  setToolApproval,
  type ToolEntry,
  type ToolIndex,
  withChannelTools,
} from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

const EMPTY_ACTIONS: RegistryActionReadMinimal[] = []
const EMPTY_TOOL_TUNNELS: Record<string, string> = {}
const NO_CHANNEL_TOOLS: AgentPresetChannelToolGroup[] = []
const NO_FAILED_PLUGINS: string[] = []
/** MCP tools take their tunnel from the MCP integration, not per tool. */
function isRegistryToolKey(key: string): boolean {
  return !key.startsWith("mcp.") && !key.startsWith("mcp__")
}

/** One integration's selected tools, as listed in the rail. */
type RailToolGroup = {
  key: string
  title: string
  namespace: string
  icon: ToolEntry
  entries: ToolEntry[]
  selected: ToolEntry[]
  approvals: number
}

type RailToolsData = {
  index: ToolIndex
  groups: RailToolGroup[]
  integrations: Array<{ id: string; entry?: ToolEntry; approvals: number }>
  unknown: string[]
  selectedCount: number
  approvalCount: number
}

function useRailToolsData(
  registryActions: RegistryActionReadMinimal[] | undefined,
  mcpIntegrations: MCPIntegrationRead[],
  ready: boolean,
  channelTools: AgentPresetChannelToolGroup[]
): RailToolsData & ReturnType<typeof usePresetToolFields> {
  const { value, onChange } = usePresetToolFields()
  const index = useMemo(
    () =>
      withChannelTools(
        buildToolIndex(registryActions ?? EMPTY_ACTIONS, mcpIntegrations),
        channelTools
      ),
    [registryActions, mcpIntegrations, channelTools]
  )
  const data = useMemo(() => {
    const selectedKeys = new Set(value.actions)
    const merged = new Map<string, RailToolGroup>()
    for (const group of index.groups) {
      const entries = group.entries.filter(
        (entry) => entry.target === "actions"
      )
      if (!entries.length) continue
      const key = group.section === "mcp" ? group.id : group.namespace
      const existing = merged.get(key)
      if (existing) {
        existing.entries.push(...entries)
        continue
      }
      merged.set(key, {
        key,
        title: group.title,
        namespace: group.namespace,
        icon: entries[0],
        entries: [...entries],
        selected: [],
        approvals: 0,
      })
    }
    let approvalCount = 0
    const groups: RailToolGroup[] = []
    for (const group of merged.values()) {
      group.selected = group.entries.filter((entry) =>
        selectedKeys.has(entry.key)
      )
      if (!group.selected.length) continue
      group.approvals = group.selected.filter((entry) =>
        getToolApproval(value.toolApprovals, entry.key, index)
      ).length
      approvalCount += group.approvals
      groups.push(group)
    }
    groups.sort((a, b) => a.title.localeCompare(b.title))
    const integrations = value.mcpIntegrations.map((id) => {
      const entry = index.byKey.get(`mcp:${id}`)
      const integration = entry?.integration
      const approvals =
        integration && integration.server_type !== "stdio"
          ? (integration.tools?.filter(
              (tool) =>
                tool.enabled !== false &&
                tool.status !== "missing" &&
                tool.requires_approval
            ).length ?? 0)
          : 0
      approvalCount += approvals
      return { id, entry, approvals }
    })
    const unknown = ready
      ? value.actions.filter((key) => !index.byKey.has(key))
      : []
    const selectedCount =
      groups.reduce((total, group) => total + group.selected.length, 0) +
      integrations.length +
      unknown.length
    return {
      index,
      groups,
      integrations,
      unknown,
      selectedCount,
      approvalCount,
    }
  }, [index, ready, value])
  return { ...data, value, onChange }
}

/** Tools grouped by integration, with counts and approval markers. */
export function AgentPresetRailTools({
  registryActions,
  mcpIntegrations,
  registryLoading,
  mcpLoading,
  toolsLoadError,
  isSaving,
  maxTools,
  skillActions,
  channelTools = NO_CHANNEL_TOOLS,
  failedChannelPlugins = NO_FAILED_PLUGINS,
  onOpenGroup,
}: {
  registryActions?: RegistryActionReadMinimal[]
  mcpIntegrations: MCPIntegrationRead[]
  registryLoading: boolean
  mcpLoading: boolean
  toolsLoadError: boolean
  isSaving: boolean
  maxTools?: number | null
  skillActions?: string[] | null
  /** Granted by the agent's channel connections; empty when not connected. */
  channelTools?: AgentPresetChannelToolGroup[]
  /** Plugins whose channel tools could not be read. */
  failedChannelPlugins?: string[]
  onOpenGroup: (groupKey: string, title: string) => void
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const loading = registryLoading || mcpLoading
  const ready = !loading && !toolsLoadError
  const {
    index,
    groups,
    integrations,
    unknown,
    selectedCount,
    approvalCount: groupApprovalCount,
    value,
    onChange,
  } = useRailToolsData(registryActions, mcpIntegrations, ready, channelTools)
  const selected = new Set(value.actions)
  const skillOnly = (skillActions ?? []).filter((key) => !selected.has(key))
  const channelKeys = channelTools.flatMap((group) =>
    group.tools.map((tool) => tool.name)
  )
  // Tools granted outside the list, by a skill or a channel connection.
  const grantedKeys = [...skillOnly, ...channelKeys]
  const granted = new Set(grantedKeys)
  const otherRules = value.toolApprovals.filter(
    (rule) => rule.allow && !selected.has(rule.tool) && !granted.has(rule.tool)
  )
  // Superseded groups cannot run while connected, so their asks do not count.
  const supersededApprovals = groups
    .filter((group) => supersededBy(group.namespace, channelTools))
    .reduce((total, group) => total + group.approvals, 0)
  const approvalCount =
    groupApprovalCount -
    supersededApprovals +
    grantedKeys.filter((key) =>
      getToolApproval(value.toolApprovals, key, index)
    ).length
  const blocked = getBlockedActions(value.actions, value.namespaces)

  function toggleApproval(key: string, ask: boolean) {
    const next = setToolApproval(value.toolApprovals, key, !ask, index)
    if (next !== value.toolApprovals)
      onChange({ ...value, toolApprovals: next })
  }

  function removeIntegration(id: string) {
    onChange({
      ...value,
      mcpIntegrations: value.mcpIntegrations.filter((key) => key !== id),
    })
  }

  return (
    <section>
      <RailSectionHeader
        title="Tools"
        count={ready ? selectedCount + channelKeys.length : undefined}
        extra={
          approvalCount > 0 ? (
            <span
              className={cn(
                "ml-2.5 flex items-center gap-1.5 text-xs",
                APPROVAL_TEXT
              )}
            >
              <span className={APPROVAL_DOT} />
              {approvalCount} need{approvalCount === 1 ? "s" : ""} approval
            </span>
          ) : null
        }
        addLabel="Add tools"
        onAdd={() => setPickerOpen(true)}
        addDisabled={!ready || isSaving}
      />
      {loading ? <RailEmpty>Loading tools...</RailEmpty> : null}
      {toolsLoadError ? (
        <RailEmpty>
          <span className="text-destructive">Unable to load tools.</span>
        </RailEmpty>
      ) : null}
      {ready &&
      selectedCount === 0 &&
      skillOnly.length === 0 &&
      channelKeys.length === 0 &&
      otherRules.length === 0 ? (
        <RailEmpty />
      ) : null}
      {ready
        ? groups.map((group) => {
            const replacedBy = supersededBy(group.namespace, channelTools)
            if (replacedBy) {
              return (
                <SupersededGroupRow
                  key={group.key}
                  group={group}
                  replacedBy={replacedBy}
                  onOpen={() => onOpenGroup(group.key, group.title)}
                />
              )
            }
            return (
              <button
                key={group.key}
                type="button"
                className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}
                onClick={() => onOpenGroup(group.key, group.title)}
              >
                <ToolLogo entry={group.icon} />
                <span className="shrink-0 whitespace-nowrap text-[13.5px]">
                  {group.title}
                </span>
                <span className="min-w-0 truncate font-mono text-[11.5px] text-muted-foreground/70">
                  {group.namespace}
                </span>
                <span className="flex-1" />
                {group.approvals > 0 ? (
                  <span
                    className={APPROVAL_DOT}
                    title={`${group.approvals} ${group.approvals === 1 ? "needs" : "need"} approval`}
                  />
                ) : null}
                <span className="min-w-12 whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground">
                  {group.selected.length} of {group.entries.length}
                </span>
              </button>
            )
          })
        : null}
      {ready
        ? integrations.map(({ id, entry, approvals }) => (
            <div key={id} className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}>
              {entry ? <ToolLogo entry={entry} /> : <LogoTile>?</LogoTile>}
              <span className="shrink-0 whitespace-nowrap text-[13.5px]">
                {entry?.title ?? "Unavailable MCP server"}
              </span>
              <span className="min-w-0 truncate font-mono text-[11.5px] text-muted-foreground/70">
                {entry?.integration?.slug ?? id}
              </span>
              <span className="flex-1" />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={RAIL_REMOVE_BUTTON}
                aria-label={`Remove ${entry?.title ?? id}`}
                disabled={isSaving}
                onClick={() => removeIntegration(id)}
              >
                <X className="size-3" />
              </Button>
              {approvals > 0 ? (
                <span
                  className={APPROVAL_DOT}
                  title={`${approvals} ${approvals === 1 ? "needs" : "need"} approval`}
                />
              ) : null}
              <span className="min-w-12 whitespace-nowrap text-right text-xs text-muted-foreground">
                MCP · all
              </span>
            </div>
          ))
        : null}
      {ready && unknown.length > 0 ? (
        <button
          type="button"
          className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}
          onClick={() => onOpenGroup(UNAVAILABLE_GROUP, "Unavailable")}
        >
          <LogoTile>?</LogoTile>
          <span className="text-[13.5px] text-destructive">Unavailable</span>
          <span className="flex-1" />
          <span className="min-w-12 text-right text-xs text-muted-foreground">
            {unknown.length}
          </span>
        </button>
      ) : null}
      {ready && skillOnly.length > 0 ? (
        <div className="pt-1">
          <p className="px-2 pb-0.5 text-xs text-muted-foreground/70">
            From skills
          </p>
          {skillOnly.map((key) => {
            const ask = getToolApproval(value.toolApprovals, key, index)
            const entry = index.byKey.get(key)
            return (
              <div key={key} className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}>
                {entry ? <ToolLogo entry={entry} /> : <LogoTile>?</LogoTile>}
                <span className="min-w-0 shrink truncate text-[13.5px]">
                  {entry?.title ?? key}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/70">
                  {key}
                </span>
                {/* Pull the label flush with the counts in the group rows. */}
                <ApprovalButton
                  tool={key}
                  ask={ask}
                  disabled={isSaving}
                  onToggle={() => toggleApproval(key, ask)}
                  className="-mr-2"
                />
              </div>
            )
          })}
        </div>
      ) : null}
      {!loading &&
      (channelTools.length > 0 || failedChannelPlugins.length > 0) ? (
        <ChannelToolsSection
          channelTools={channelTools}
          failedPlugins={failedChannelPlugins}
          toolApprovals={value.toolApprovals}
          index={index}
          onOpenGroup={onOpenGroup}
        />
      ) : null}
      {ready && otherRules.length > 0 ? (
        <div className="pt-1">
          <p className="px-2 pb-0.5 text-xs text-muted-foreground/70">
            Other approval rules
          </p>
          {otherRules.map(({ tool }) => {
            const entry = index.byKey.get(tool)
            return (
              <div key={tool} className={cn(RAIL_LIST_ROW, "h-[34px] py-0")}>
                {entry ? <ToolLogo entry={entry} /> : <LogoTile>?</LogoTile>}
                <span className="min-w-0 shrink truncate text-[13.5px]">
                  {entry?.title ?? tool}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/70">
                  {entry ? tool : null}
                </span>
                <ApprovalButton
                  tool={tool}
                  ask
                  disabled={isSaving}
                  onToggle={() => toggleApproval(tool, true)}
                  className="-mr-2"
                />
              </div>
            )
          })}
        </div>
      ) : null}
      {ready && value.namespaces.length > 0 ? (
        <div className="flex items-center gap-2 px-2 pt-1 text-xs text-muted-foreground">
          <span className="min-w-0 flex-1">
            {blocked.length} listed {blocked.length === 1 ? "tool" : "tools"}{" "}
            blocked by the stored namespace filter
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            disabled={isSaving}
            onClick={() => onChange({ ...value, namespaces: [] })}
          >
            Clear
          </Button>
        </div>
      ) : null}
      <ToolPickerDialog
        index={index}
        open={pickerOpen && ready}
        onOpenChange={setPickerOpen}
        maxTools={maxTools}
        skillActions={skillActions ?? undefined}
        disabled={isSaving}
        value={value}
        onChange={onChange}
      />
    </section>
  )
}

/** Group key for selected tools the catalog no longer lists. */
export const UNAVAILABLE_GROUP = "__unavailable__"

type RailToolGroupProps = {
  groupKey: string
  registryActions?: RegistryActionReadMinimal[]
  mcpIntegrations: MCPIntegrationRead[]
  registryLoading: boolean
  mcpLoading: boolean
  toolsLoadError: boolean
  isSaving: boolean
  maxTools?: number | null
  skillActions?: string[] | null
  channelTools?: AgentPresetChannelToolGroup[]
  /** Called after "Remove all" empties the group. */
  onEmptied?: () => void
}

/** Drill-in for one tool group: a selected integration or a channel connection. */
export function AgentPresetRailToolGroup(props: RailToolGroupProps) {
  const ready =
    !props.registryLoading && !props.mcpLoading && !props.toolsLoadError
  const channelTools = props.channelTools ?? NO_CHANNEL_TOOLS
  const data = useRailToolsData(
    props.registryActions,
    props.mcpIntegrations,
    ready,
    channelTools
  )
  if (isChannelGroupKey(props.groupKey)) {
    return (
      <ChannelToolGroupDetail
        groupKey={props.groupKey}
        channelTools={channelTools}
        index={data.index}
        isSaving={props.isSaving}
      />
    )
  }
  return (
    <RegistryToolGroupDetail
      {...props}
      channelTools={channelTools}
      ready={ready}
      data={data}
    />
  )
}

/** Drill-in for one integration: per-tool approval and removal. */
function RegistryToolGroupDetail({
  groupKey,
  toolsLoadError,
  isSaving,
  maxTools,
  skillActions,
  channelTools,
  onEmptied,
  ready,
  data: { index, groups, unknown, value, onChange },
}: RailToolGroupProps & {
  channelTools: AgentPresetChannelToolGroup[]
  ready: boolean
  data: ReturnType<typeof useRailToolsData>
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const group = groups.find((candidate) => candidate.key === groupKey)
  const superseded = group
    ? supersededBy(group.namespace, channelTools) !== undefined
    : false
  const isUnavailable = groupKey === UNAVAILABLE_GROUP
  const { control, setValue } = useFormContext<{
    toolTunnels: Record<string, string>
  }>()
  const toolTunnels =
    useWatch({ control, name: "toolTunnels" }) ?? EMPTY_TOOL_TUNNELS
  const rows: Array<{ key: string; title: string; ask: boolean }> =
    isUnavailable
      ? unknown.map((key) => ({
          key,
          title: key,
          ask: getToolApproval(value.toolApprovals, key, index),
        }))
      : (group?.selected ?? []).map((entry) => ({
          key: entry.key,
          title: entry.title,
          ask: getToolApproval(value.toolApprovals, entry.key, index),
        }))

  function toggleApproval(key: string, ask: boolean) {
    const next = setToolApproval(value.toolApprovals, key, !ask, index)
    if (next !== value.toolApprovals)
      onChange({ ...value, toolApprovals: next })
  }

  function setTunnel(key: string, tunnel: string | undefined) {
    const next = { ...toolTunnels }
    if (tunnel) next[key] = tunnel
    else delete next[key]
    setValue("toolTunnels", next, { shouldDirty: true })
  }

  function remove(key: string) {
    const next = removeTools(
      { actions: value.actions, toolApprovals: value.toolApprovals },
      new Set([key]),
      index,
      new Set(skillActions ?? [])
    )
    onChange({ ...value, ...next })
  }

  function setAllApprovals(ask: boolean) {
    let next = value.toolApprovals
    for (const row of rows) {
      next = setToolApproval(next, row.key, ask, index)
    }
    if (next !== value.toolApprovals)
      onChange({ ...value, toolApprovals: next })
  }

  function removeAll() {
    const next = removeTools(
      { actions: value.actions, toolApprovals: value.toolApprovals },
      new Set(rows.map((row) => row.key)),
      index,
      new Set(skillActions ?? [])
    )
    onChange({ ...value, ...next })
    onEmptied?.()
  }

  const allAsk = rows.length > 0 && rows.every((row) => row.ask)
  const allAuto = rows.length > 0 && rows.every((row) => !row.ask)

  if (toolsLoadError) {
    return (
      <RailEmpty>
        <span className="text-destructive">Unable to load tools.</span>
      </RailEmpty>
    )
  }
  if (!ready) {
    return <RailEmpty>Loading tools...</RailEmpty>
  }
  if (!rows.length) {
    return <RailEmpty>No tools selected here.</RailEmpty>
  }

  return (
    <div>
      {group ? (
        <div className="mb-2 flex items-center gap-2.5 px-2 text-[13px]">
          <ToolLogo entry={group.icon} />
          <span className="font-mono text-[11.5px] text-muted-foreground">
            {group.namespace}
          </span>
          <span className="flex-1" />
          <span className="text-xs text-muted-foreground">
            {group.selected.length} of {group.entries.length} selected
          </span>
        </div>
      ) : null}
      <div className={cn(superseded && "opacity-50")}>
        <div className="mb-1 flex h-8 items-center gap-2 border-b border-border/60 pr-0.5 pl-2">
          <span className="flex-1 text-xs text-muted-foreground">
            {rows.length} {rows.length === 1 ? "tool" : "tools"}
          </span>
          <ApprovalForAll
            allAsk={allAsk}
            allAuto={allAuto}
            disabled={isSaving}
            onSet={setAllApprovals}
          />
        </div>
        {rows.map((row) => (
          <div key={row.key} className={cn(RAIL_LIST_ROW, "h-9 py-0")}>
            <span className="min-w-0 shrink truncate">{row.title}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground/70">
              {isUnavailable ? "" : row.key}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={RAIL_REMOVE_BUTTON}
              aria-label={`Remove ${row.title}`}
              disabled={isSaving}
              onClick={() => remove(row.key)}
            >
              <X className="size-3" />
            </Button>
            {isRegistryToolKey(row.key) ? (
              <TunnelSelect
                value={toolTunnels[row.key]}
                onChange={(next) => setTunnel(row.key, next)}
                noneLabel="No tunnel"
                disabled={isSaving}
                className="h-7 w-28 shrink-0 shadow-none"
              />
            ) : null}
            <ApprovalButton
              tool={row.key}
              ask={row.ask}
              disabled={isSaving}
              onToggle={() => toggleApproval(row.key, row.ask)}
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2 px-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 text-xs shadow-none"
          onClick={() => setPickerOpen(true)}
          disabled={isSaving}
        >
          Add tools
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={cn(
            "h-7 text-xs font-normal text-muted-foreground hover:text-destructive",
            RAIL_FOCUS
          )}
          disabled={isSaving}
          onClick={removeAll}
        >
          Remove all
        </Button>
      </div>
      <ToolPickerDialog
        index={index}
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        maxTools={maxTools}
        skillActions={skillActions ?? undefined}
        disabled={isSaving}
        value={value}
        onChange={onChange}
        initialSource={index.groups
          .filter(
            (candidate) =>
              (candidate.section === "mcp"
                ? candidate.id
                : candidate.namespace) === groupKey
          )
          .map((candidate) => candidate.id)}
      />
    </div>
  )
}
