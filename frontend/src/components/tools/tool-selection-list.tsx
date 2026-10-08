"use client"

import { ChevronDown, ChevronRight, Plus, Search, X } from "lucide-react"
import { memo, useMemo, useState } from "react"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { getIcon, getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import {
  ToolIcon,
  ToolPickerDialog,
} from "@/components/tools/tool-picker-dialog"
import { Badge, badgeVariants } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Input } from "@/components/ui/input"
import { Item } from "@/components/ui/item"
import {
  buildToolIndex,
  getBlockedActions,
  getToolApproval,
  removeTools,
  setToolApproval,
  type ToolEntry,
  type ToolGroup,
  type ToolIndex,
  type ToolSelectionValue,
} from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

const COUNT_BADGE =
  "h-5 min-w-14 shrink-0 justify-center px-2 text-[10px] font-normal tabular-nums"
const APPROVAL_COLOR = "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400"
const FOCUS =
  "focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
const REVEAL =
  "opacity-0 disabled:opacity-0 group-hover:opacity-100 group-has-[:focus-visible]:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
const EMPTY_ACTIONS: RegistryActionReadMinimal[] = []
const EMPTY_INTEGRATIONS: MCPIntegrationRead[] = []

const EMPTY_KEYS: string[] = []
const EMPTY_RULES: NonNullable<ToolSelectionValue["toolApprovals"]> = []

/** Props for the form-agnostic, controlled tools list. */
export interface ToolSelectionListProps {
  value: ToolSelectionValue
  /** Called only with a value whose changed fields have new references. */
  onChange: (value: ToolSelectionValue) => void
  /** Prebuilt catalogue; replaces the one built from the two props below. */
  index?: ToolIndex
  registryActions?: RegistryActionReadMinimal[]
  mcpIntegrations?: MCPIntegrationRead[]
  isSaving?: boolean
  /** Disable "Add tools" while keeping removal available. Defaults to false. */
  addDisabled?: boolean
  maxTools?: number | null
  registryLoading?: boolean
  mcpLoading?: boolean
  toolsLoadError?: boolean
  skillActions?: string[] | null
  /** Show Ask/Auto toggles, approval badges and rules. Defaults to true. */
  approvalsEnabled?: boolean
  /** Show MCP groups and rows. Defaults to true. */
  mcpEnabled?: boolean
  /** Drop actions agents cannot call from the built catalogue. Defaults to true. */
  filterAgentTools?: boolean
  /** Hide the "Tools" title when a surrounding label already names the field. */
  hideTitle?: boolean
  /** Called instead of selecting when a locked entry is chosen in the picker. */
  onLockedSelect?: (entry: ToolEntry) => void
}

/** Controlled selected-tools list with an integrated picker, in stored order. */
export const ToolSelectionList = memo(function ToolSelectionList({
  index: providedIndex,
  registryActions = EMPTY_ACTIONS,
  mcpIntegrations = EMPTY_INTEGRATIONS,
  isSaving = false,
  addDisabled = false,
  maxTools,
  registryLoading = false,
  mcpLoading = false,
  toolsLoadError = false,
  skillActions,
  value,
  onChange,
  approvalsEnabled = true,
  mcpEnabled = true,
  filterAgentTools = true,
  hideTitle = false,
  onLockedSelect,
}: ToolSelectionListProps) {
  const actions = value.actions
  const integrations = mcpEnabled ? value.mcpIntegrations : EMPTY_KEYS
  const namespaces = value.namespaces ?? EMPTY_KEYS
  const rules = value.toolApprovals ?? EMPTY_RULES
  const index = useMemo(
    () =>
      providedIndex ??
      buildToolIndex(
        registryActions,
        mcpEnabled ? mcpIntegrations : EMPTY_INTEGRATIONS,
        { filterAgentTools }
      ),
    [
      providedIndex,
      filterAgentTools,
      mcpEnabled,
      registryActions,
      mcpIntegrations,
    ]
  )
  // Entries written to `actions` are listed tool by tool under their group,
  // whichever section they come from. Registry namespaces that span both
  // registries are merged into one group.
  const toolGroups = useMemo(() => {
    const groups = new Map<string, ToolGroup>()
    const names = new Map<string, string>()
    for (const action of registryActions) {
      if (action.display_group && !names.has(action.namespace)) {
        names.set(action.namespace, action.display_group)
      }
    }
    for (const group of index.groups) {
      const isMcp = group.section === "mcp"
      if (isMcp && !mcpEnabled) continue
      const entries = group.entries.filter(
        (entry) => entry.target === "actions"
      )
      if (!entries.length) continue
      const id = isMcp ? group.id : group.namespace
      const existing = groups.get(id)
      if (existing) existing.entries.push(...entries)
      else
        groups.set(id, {
          ...group,
          title: (!isMcp && names.get(group.namespace)) || group.title,
          entries,
        })
    }
    return [...groups.values()].sort(
      (a, b) =>
        a.title.localeCompare(b.title) || a.namespace.localeCompare(b.namespace)
    )
  }, [index, mcpEnabled, registryActions])
  // Entries written to `mcpIntegrations` grant a whole integration in one row.
  const integrationsById = useMemo(
    () =>
      new Map(
        index.entries
          .filter((entry) => entry.target === "mcpIntegrations")
          .map((entry) => [entry.key, entry.integration])
      ),
    [index]
  )
  const defaultAskKeys = useMemo(
    () =>
      new Set(
        index.entries
          .filter((entry) => entry.defaultAsk)
          .map((entry) => entry.key)
      ),
    [index]
  )
  const [open, setOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const selected = new Set(actions)
  function asksForApproval(key: string) {
    return getToolApproval(rules, key, index)
  }
  const blocked = getBlockedActions(actions, namespaces)
  const loading = registryLoading || mcpLoading
  const ready = !loading && !toolsLoadError
  const canAdd = ready && !addDisabled
  // Forget a pending open when the picker is hidden, so it does not reopen by
  // itself once the catalogue is ready again.
  if (open && !canAdd) setOpen(false)
  // The field header counts every listed grant, MCP servers included. The
  // titled header keeps counting catalogue tools only.
  const selectedCount =
    actions.filter((key) => index.byKey.has(key)).length +
    (hideTitle ? integrations.length : 0)
  const skillKeys = new Set(skillActions?.filter((key) => !selected.has(key)))
  const unavailable = ready
    ? actions.filter((key) => !index.byKey.has(key))
    : []
  const otherRules = rules.filter(
    (rule) =>
      rule.allow && !selected.has(rule.tool) && !skillKeys.has(rule.tool)
  )
  const search = query.trim().toLowerCase()

  function matches(...values: string[]) {
    return values.some((value) => value.toLowerCase().includes(search))
  }

  const visibleUnavailable = unavailable.filter((key) => matches(key))
  const visibleOtherRules = otherRules.filter((rule) => matches(rule.tool))
  const visibleSkillKeys = [...skillKeys].filter((key) =>
    matches(key, index.byKey.get(key)?.title ?? "")
  )
  const visibleIntegrations = integrations.filter((id) => {
    const integration = integrationsById.get(id)
    return matches(integration?.name ?? id, integration?.slug ?? "")
  })
  const hasVisibleRegistry = toolGroups.some((group) =>
    group.entries.some(
      (entry) =>
        selected.has(entry.key) && matches(entry.title, entry.key, group.title)
    )
  )
  const hasMatches =
    hasVisibleRegistry ||
    visibleIntegrations.length > 0 ||
    visibleUnavailable.length > 0 ||
    visibleOtherRules.length > 0 ||
    visibleSkillKeys.length > 0
  const blockedNotice = `${blocked.length} listed ${blocked.length === 1 ? "tool" : "tools"} blocked by the stored namespace filter`

  function writeRules(next: typeof rules) {
    if (next !== rules) onChange({ ...value, toolApprovals: next })
  }

  function changeApproval(key: string, ask: boolean) {
    writeRules(setToolApproval(rules, key, ask, defaultAskKeys.has(key)))
  }

  function remove(keys: string[]) {
    const next = removeTools(
      { actions, toolApprovals: rules },
      new Set(keys),
      defaultAskKeys,
      new Set(skillActions)
    )
    if (next.actions === actions && next.toolApprovals === rules) return
    const changed = { ...value, actions: next.actions }
    if (next.toolApprovals !== rules) changed.toolApprovals = next.toolApprovals
    onChange(changed)
  }

  function groupApproval(keys: string[], ask: boolean) {
    let next = rules
    for (const key of keys)
      next = setToolApproval(next, key, ask, defaultAskKeys.has(key))
    writeRules(next)
  }

  function removeIntegration(id: string) {
    const current = value.mcpIntegrations
    if (current.includes(id))
      onChange({
        ...value,
        mcpIntegrations: current.filter((key) => key !== id),
      })
  }

  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-center gap-2">
        {!hideTitle && <h3 className="text-xs font-medium">Tools</h3>}
        {/* Nothing can be counted until the catalogue has loaded. */}
        {ready && (
          <span className="text-xs text-muted-foreground">
            {selectedCount}
            {hideTitle && " selected"}
          </span>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={cn("ml-auto size-7", FOCUS)}
          aria-label="Search allowed tools"
          aria-expanded={searchOpen}
          onClick={() => {
            setSearchOpen(!searchOpen)
            setQuery("")
          }}
        >
          <Search className="size-3.5" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn("h-7 gap-1.5 text-xs shadow-none", FOCUS)}
          disabled={isSaving || !canAdd}
          onClick={() => setOpen(true)}
        >
          <Plus className="size-3.5" />
          Add tools
        </Button>
      </div>
      {searchOpen && (
        <div className="flex items-center gap-2">
          <Search className="size-4 text-muted-foreground" />
          <Input
            autoFocus
            aria-label="Filter allowed tools"
            placeholder="Search allowed tools"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-8 border-0 bg-transparent p-0 shadow-none focus-visible:ring-0"
          />
        </div>
      )}
      {namespaces.length > 0 && (
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate" title={blockedNotice}>
            {blockedNotice}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={isSaving}
            onClick={() => onChange({ ...value, namespaces: [] })}
          >
            Clear
          </Button>
        </div>
      )}
      {ready &&
        !search &&
        actions.length === 0 &&
        integrations.length === 0 &&
        skillKeys.size === 0 &&
        otherRules.length === 0 && (
          <p className="py-3 text-xs text-muted-foreground">
            No tools selected.
          </p>
        )}
      {!ready && (
        <p className="py-3 text-xs text-muted-foreground">
          {toolsLoadError ? "Tools could not be loaded." : "Loading tools..."}
        </p>
      )}
      {ready && search && !hasMatches && (
        <p className="py-3 text-xs text-muted-foreground">No matching tools</p>
      )}
      {ready && (
        <div className="divide-y divide-border/50">
          {toolGroups.map((group) => {
            const allowed = group.entries.filter((entry) =>
              selected.has(entry.key)
            )
            const visible = allowed.filter((entry) =>
              matches(entry.title, entry.key, group.title)
            )
            if (!visible.length) return null
            const keys = allowed.map((entry) => entry.key)
            const approvalCount = approvalsEnabled
              ? keys.filter((key) => asksForApproval(key)).length
              : 0
            const isExpanded = expanded.has(group.id) || Boolean(search)
            return (
              <div key={group.id}>
                <ContextMenu>
                  <ContextMenuTrigger asChild>
                    <button
                      type="button"
                      aria-expanded={isExpanded}
                      onClick={() =>
                        setExpanded((current) => {
                          const next = new Set(current)
                          if (next.has(group.id)) next.delete(group.id)
                          else next.add(group.id)
                          return next
                        })
                      }
                      className="flex h-10 w-full min-w-0 items-center gap-2 rounded px-1 py-2 text-left outline-none hover:bg-muted/50 data-[state=open]:bg-muted/70 focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                    >
                      {isExpanded ? (
                        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                      )}
                      <ToolIcon entry={group.entries[0]} />
                      <span className="min-w-0 shrink-[1] truncate text-xs font-medium">
                        {group.title}
                      </span>
                      <span className="min-w-0 shrink-[100] truncate font-mono text-[10px] text-muted-foreground">
                        {group.namespace}
                      </span>
                      <span className="ml-auto flex shrink-0 items-center gap-1">
                        {approvalCount > 0 && (
                          <span
                            className={cn(
                              badgeVariants({ variant: "secondary" }),
                              COUNT_BADGE,
                              APPROVAL_COLOR
                            )}
                          >
                            {approvalCount} need approval
                          </span>
                        )}
                        <span
                          className={cn(
                            badgeVariants({ variant: "secondary" }),
                            COUNT_BADGE
                          )}
                        >
                          {allowed.length} of {group.entries.length}
                        </span>
                      </span>
                    </button>
                  </ContextMenuTrigger>
                  <ContextMenuContent className="w-52 shadow-none">
                    {approvalsEnabled && (
                      <>
                        <ContextMenuItem
                          className="text-xs"
                          disabled={isSaving}
                          onSelect={() => groupApproval(keys, true)}
                        >
                          Require approval for all
                        </ContextMenuItem>
                        <ContextMenuItem
                          className="text-xs"
                          disabled={isSaving}
                          onSelect={() => groupApproval(keys, false)}
                        >
                          Run all automatically
                        </ContextMenuItem>
                      </>
                    )}
                    <ContextMenuItem
                      className="text-xs text-rose-500 focus:text-rose-600"
                      disabled={isSaving}
                      onSelect={() => remove(keys)}
                    >
                      Remove all
                    </ContextMenuItem>
                  </ContextMenuContent>
                </ContextMenu>
                {isExpanded &&
                  visible.map((entry) => (
                    <ToolSelectionRow
                      key={entry.key}
                      tool={entry.key}
                      title={entry.title}
                      ask={asksForApproval(entry.key)}
                      disabled={isSaving}
                      onApproval={
                        approvalsEnabled
                          ? () =>
                              changeApproval(
                                entry.key,
                                !asksForApproval(entry.key)
                              )
                          : undefined
                      }
                      onRemove={() => remove([entry.key])}
                    />
                  ))}
              </div>
            )
          })}
          {visibleIntegrations.map((id) => {
            const integration = integrationsById.get(id)
            const approvalCount =
              approvalsEnabled && integration?.server_type !== "stdio"
                ? (integration?.tools?.filter(
                    (tool) =>
                      tool.enabled !== false &&
                      tool.status !== "missing" &&
                      tool.requires_approval
                  ).length ?? 0)
                : 0
            return (
              <Item
                key={id}
                className="group flex-nowrap gap-2 rounded-none border-0 h-10 px-1 py-0 hover:bg-muted/50"
              >
                <ProviderIcon
                  providerId={getMcpProviderIconId(
                    integration?.slug ?? "custom"
                  )}
                  className="size-6 shrink-0 rounded border"
                />
                <span className="min-w-0 flex-1 truncate text-xs">
                  {integration?.name ?? id}
                </span>
                <RemoveButton
                  label={integration?.name ?? id}
                  disabled={isSaving}
                  onClick={() => removeIntegration(id)}
                />
                <Badge variant="outline" className={COUNT_BADGE}>
                  MCP
                </Badge>
                {approvalCount > 0 && (
                  <Badge
                    variant="secondary"
                    className={cn(COUNT_BADGE, APPROVAL_COLOR)}
                  >
                    {approvalCount} need approval
                  </Badge>
                )}
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  All tools
                </span>
              </Item>
            )
          })}
          {visibleSkillKeys.length > 0 && (
            <div>
              <h4 className="py-3 text-xs text-muted-foreground">
                From skills
              </h4>
              {visibleSkillKeys.map((key) => (
                <ToolSelectionRow
                  key={key}
                  tool={key}
                  title={index.byKey.get(key)?.title}
                  showIcon
                  ask={asksForApproval(key)}
                  disabled={isSaving}
                  onApproval={
                    approvalsEnabled
                      ? () => changeApproval(key, !asksForApproval(key))
                      : undefined
                  }
                />
              ))}
            </div>
          )}
          {visibleUnavailable.length > 0 && (
            <div>
              <h4 className="py-3 text-xs text-muted-foreground">
                Unavailable
              </h4>
              {visibleUnavailable.map((key) => (
                <ToolSelectionRow
                  key={key}
                  tool={key}
                  disabled={isSaving}
                  onRemove={() => remove([key])}
                />
              ))}
            </div>
          )}
          {approvalsEnabled && visibleOtherRules.length > 0 && (
            <div>
              <h4 className="py-3 text-xs text-muted-foreground">
                Other approval rules
              </h4>
              {visibleOtherRules.map((rule) => (
                <ToolSelectionRow
                  key={rule.tool}
                  tool={rule.tool}
                  ask
                  disabled={isSaving}
                  onApproval={() => changeApproval(rule.tool, false)}
                  onRemove={() => changeApproval(rule.tool, false)}
                />
              ))}
            </div>
          )}
        </div>
      )}
      <ToolPickerDialog
        index={index}
        open={open && canAdd}
        onOpenChange={setOpen}
        maxTools={maxTools}
        skillActions={skillActions ?? undefined}
        disabled={isSaving}
        value={value}
        onChange={onChange}
        approvalsEnabled={approvalsEnabled}
        mcpEnabled={mcpEnabled}
        onLockedSelect={onLockedSelect}
      />
    </section>
  )
})

/** One selected tool: optional title, its ID, and remove/approval controls. */
export function ToolSelectionRow({
  tool,
  title,
  ask,
  disabled,
  onApproval,
  onRemove,
  showIcon,
}: {
  tool: string
  title?: string
  ask?: boolean
  disabled: boolean
  onApproval?: () => void
  onRemove?: () => void
  showIcon?: boolean
}) {
  return (
    <Item className="group h-[34px] flex-nowrap gap-2 rounded-none border-0 py-0 pl-7 pr-1 hover:bg-muted/50">
      {showIcon &&
        getIcon(tool, { className: "size-5 shrink-0 rounded border" })}
      {title && <span className="w-32 shrink-0 truncate text-xs">{title}</span>}
      <span
        className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted-foreground"
        title={tool}
      >
        {tool}
      </span>
      {onRemove && (
        <RemoveButton label={tool} disabled={disabled} onClick={onRemove} />
      )}
      {onApproval && (
        <HoverCard openDelay={300}>
          <HoverCardTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Require approval for ${tool}`}
              aria-pressed={ask}
              disabled={disabled}
              onClick={onApproval}
              className={cn(
                "h-6 shrink-0 px-2 text-[10px]",
                FOCUS,
                ask && APPROVAL_COLOR
              )}
            >
              {ask ? "Ask" : "Auto"}
            </Button>
          </HoverCardTrigger>
          <HoverCardContent
            align="end"
            collisionPadding={8}
            className="w-64 p-3 text-xs"
          >
            {ask
              ? "This tool call needs human-in-the-loop approval. Click to run it automatically."
              : "Click to require human-in-the-loop approval for this tool call."}
          </HoverCardContent>
        </HoverCard>
      )}
    </Item>
  )
}

function RemoveButton({
  label,
  disabled,
  onClick,
}: {
  label: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className={cn("size-6 shrink-0 text-muted-foreground", REVEAL, FOCUS)}
      aria-label={`Remove ${label}`}
      disabled={disabled}
      onClick={onClick}
    >
      <X className="size-3" />
    </Button>
  )
}
