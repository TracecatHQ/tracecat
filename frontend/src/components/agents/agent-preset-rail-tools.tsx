"use client"

import { X } from "lucide-react"
import { useMemo, useState } from "react"
import { useFormContext, useWatch } from "react-hook-form"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import {
  LogoTile,
  RAIL_FOCUS,
  RAIL_LIST_ROW,
  RAIL_REMOVE_BUTTON,
  RailEmpty,
  RailSectionHeader,
} from "@/components/agents/agent-preset-rail-primitives"
import { usePresetToolFields } from "@/components/agents/agent-preset-tools-list"
import { getIcon, getMcpProviderIconId, ProviderIcon } from "@/components/icons"
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
} from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

const APPROVAL_DOT = "size-1.5 shrink-0 rounded-full bg-[#d6a14a]"
const APPROVAL_TEXT = "text-[#8a5a12] dark:text-[#b58a45]"
const EMPTY_ACTIONS: RegistryActionReadMinimal[] = []
const EMPTY_TOOL_TUNNELS: Record<string, string> = {}

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
  defaultAskKeys: Set<string>
}

function useRailToolsData(
  registryActions: RegistryActionReadMinimal[] | undefined,
  mcpIntegrations: MCPIntegrationRead[],
  ready: boolean
): RailToolsData & ReturnType<typeof usePresetToolFields> {
  const { value, onChange } = usePresetToolFields()
  const index = useMemo(
    () => buildToolIndex(registryActions ?? EMPTY_ACTIONS, mcpIntegrations),
    [registryActions, mcpIntegrations]
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
    const defaultAskKeys = new Set(
      index.entries.filter((entry) => entry.defaultAsk).map((e) => e.key)
    )
    return {
      index,
      groups,
      integrations,
      unknown,
      selectedCount,
      approvalCount,
      defaultAskKeys,
    }
  }, [index, ready, value])
  return { ...data, value, onChange }
}

function ToolLogo({ entry }: { entry: ToolEntry }) {
  if (entry.integration) {
    return (
      <LogoTile>
        <ProviderIcon
          inline
          providerId={getMcpProviderIconId(entry.integration.slug)}
          className="size-3 rounded-none bg-transparent p-0"
        />
      </LogoTile>
    )
  }
  return getIcon(entry.key, {
    className: "size-5 shrink-0 rounded-[5px] border [&_svg]:size-3",
    inline: true,
  })
}

/** Per-tool approval toggle, right-aligned in a fixed-width slot. */
function ApprovalButton({
  tool,
  ask,
  disabled,
  onToggle,
  className,
}: {
  tool: string
  ask: boolean
  disabled: boolean
  onToggle: () => void
  className?: string
}) {
  return (
    <span className={cn("flex min-w-[104px] shrink-0 justify-end", className)}>
      <button
        type="button"
        aria-pressed={ask}
        aria-label={`Require approval for ${tool}`}
        disabled={disabled}
        onClick={onToggle}
        className={cn(
          "h-[26px] whitespace-nowrap rounded-[5px] px-2 text-xs transition-colors hover:bg-foreground/[0.06]",
          RAIL_FOCUS,
          ask ? APPROVAL_TEXT : "text-muted-foreground"
        )}
      >
        {ask ? "Needs approval" : "Auto"}
      </button>
    </span>
  )
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
    defaultAskKeys,
    value,
    onChange,
  } = useRailToolsData(registryActions, mcpIntegrations, ready)
  const selected = new Set(value.actions)
  const skillOnly = (skillActions ?? []).filter((key) => !selected.has(key))
  // Ask rules for tools that are neither listed nor granted by a skill.
  const skillKeys = new Set(skillOnly)
  const otherRules = value.toolApprovals.filter(
    (rule) =>
      rule.allow && !selected.has(rule.tool) && !skillKeys.has(rule.tool)
  )
  const approvalCount =
    groupApprovalCount +
    skillOnly.filter((key) => getToolApproval(value.toolApprovals, key, index))
      .length
  const blocked = getBlockedActions(value.actions, value.namespaces)

  function toggleApproval(key: string, ask: boolean) {
    const next = setToolApproval(
      value.toolApprovals,
      key,
      !ask,
      defaultAskKeys.has(key)
    )
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
        count={ready ? selectedCount : undefined}
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
      otherRules.length === 0 ? (
        <RailEmpty />
      ) : null}
      {ready
        ? groups.map((group) => (
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
          ))
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

/** Drill-in for one integration: per-tool approval and removal. */
export function AgentPresetRailToolGroup({
  groupKey,
  registryActions,
  mcpIntegrations,
  registryLoading,
  mcpLoading,
  toolsLoadError,
  isSaving,
  maxTools,
  skillActions,
  onEmptied,
}: {
  groupKey: string
  registryActions?: RegistryActionReadMinimal[]
  mcpIntegrations: MCPIntegrationRead[]
  registryLoading: boolean
  mcpLoading: boolean
  toolsLoadError: boolean
  isSaving: boolean
  maxTools?: number | null
  skillActions?: string[] | null
  /** Called after "Remove all" empties the group. */
  onEmptied?: () => void
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const ready = !registryLoading && !mcpLoading && !toolsLoadError
  const { index, groups, unknown, value, onChange, defaultAskKeys } =
    useRailToolsData(registryActions, mcpIntegrations, ready)
  const group = groups.find((candidate) => candidate.key === groupKey)
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
    const next = setToolApproval(
      value.toolApprovals,
      key,
      !ask,
      defaultAskKeys.has(key)
    )
    if (next !== value.toolApprovals)
      onChange({ ...value, toolApprovals: next })
  }

  function setTunnel(key: string, tunnel: string | undefined) {
    const next = { ...toolTunnels }
    if (tunnel) next[key] = tunnel
    else delete next[key]
    setValue("toolTunnels", next, { shouldDirty: true })
  }

  function dropTunnels(keys: string[]) {
    if (!keys.some((key) => key in toolTunnels)) return
    const next = { ...toolTunnels }
    for (const key of keys) delete next[key]
    setValue("toolTunnels", next, { shouldDirty: true })
  }

  function remove(key: string) {
    dropTunnels([key])
    const next = removeTools(
      { actions: value.actions, toolApprovals: value.toolApprovals },
      new Set([key]),
      defaultAskKeys,
      new Set(skillActions ?? [])
    )
    onChange({ ...value, ...next })
  }

  function setAllApprovals(ask: boolean) {
    let next = value.toolApprovals
    for (const row of rows) {
      next = setToolApproval(next, row.key, ask, defaultAskKeys.has(row.key))
    }
    if (next !== value.toolApprovals)
      onChange({ ...value, toolApprovals: next })
  }

  function removeAll() {
    dropTunnels(rows.map((row) => row.key))
    const next = removeTools(
      { actions: value.actions, toolApprovals: value.toolApprovals },
      new Set(rows.map((row) => row.key)),
      defaultAskKeys,
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
      <div className="mb-1 flex h-8 items-center gap-2 border-b border-border/60 pr-0.5 pl-2">
        <span className="flex-1 text-xs text-muted-foreground">
          {rows.length} {rows.length === 1 ? "tool" : "tools"}
        </span>
        <div
          role="group"
          aria-label="Approval for all tools"
          className="flex rounded-md bg-foreground/[0.06] p-0.5"
        >
          {[
            { ask: false, label: "Auto", pressed: allAuto },
            { ask: true, label: "Needs approval", pressed: allAsk },
          ].map((option) => (
            <button
              key={option.label}
              type="button"
              aria-pressed={option.pressed}
              disabled={isSaving}
              onClick={() => setAllApprovals(option.ask)}
              className={cn(
                "h-[22px] rounded-[5px] px-2 text-xs transition-colors disabled:opacity-50",
                RAIL_FOCUS,
                option.pressed
                  ? "bg-background shadow-[0_1px_2px_rgba(0,0,0,0.08)] dark:bg-accent dark:shadow-none"
                  : "text-muted-foreground hover:text-foreground",
                option.pressed && option.ask && APPROVAL_TEXT
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
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
