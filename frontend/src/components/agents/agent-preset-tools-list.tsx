"use client"

import { ChevronDown, ChevronRight, Plus, Search, X } from "lucide-react"
import { useMemo, useState } from "react"
import { useFormContext, useWatch } from "react-hook-form"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { AgentToolPickerDialog } from "@/components/agents/agent-tool-picker-dialog"
import { getIcon, getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
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
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  buildToolIndex,
  getBlockedActions,
  type PresetToolFields,
  removeTools,
  setToolApproval,
  type ToolGroup,
} from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

const COUNT_BADGE = "h-5 shrink-0 px-2 text-[10px] font-normal"
const APPROVAL_COLOR = "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400"
const FOCUS =
  "focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
const REVEAL =
  "opacity-0 disabled:opacity-0 group-hover:opacity-100 group-has-[:focus-visible]:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
const EMPTY_ACTIONS: RegistryActionReadMinimal[] = []
const EMPTY_INTEGRATIONS: MCPIntegrationRead[] = []

/** Edit allowed tools and approvals while preserving the preset's stored field order. */
export function AgentPresetToolsList({
  registryActions = EMPTY_ACTIONS,
  mcpIntegrations = EMPTY_INTEGRATIONS,
  isSaving = false,
  maxTools,
  registryLoading = false,
  mcpLoading = false,
  toolsLoadError = false,
  effectiveActions,
  savedActions,
}: {
  registryActions?: RegistryActionReadMinimal[]
  mcpIntegrations?: MCPIntegrationRead[]
  isSaving?: boolean
  maxTools?: number | null
  registryLoading?: boolean
  mcpLoading?: boolean
  toolsLoadError?: boolean
  effectiveActions?: string[] | null
  savedActions?: string[] | null
}) {
  const { control, getValues, setValue } = useFormContext<PresetToolFields>()
  const actions = useWatch({ control, name: "actions" })
  const integrations = useWatch({ control, name: "mcpIntegrations" })
  const namespaces = useWatch({ control, name: "namespaces" })
  const rules = useWatch({ control, name: "toolApprovals" })
  const index = useMemo(
    () => buildToolIndex(registryActions, mcpIntegrations),
    [registryActions, mcpIntegrations]
  )
  const registryGroups = useMemo(() => {
    const groups = new Map<string, ToolGroup>()
    const names = new Map<string, string>()
    for (const action of registryActions) {
      if (action.display_group && !names.has(action.namespace)) {
        names.set(action.namespace, action.display_group)
      }
    }
    for (const group of index.groups) {
      if (group.section === "mcp") continue
      const existing = groups.get(group.namespace)
      if (existing) existing.entries.push(...group.entries)
      else
        groups.set(group.namespace, {
          ...group,
          title: names.get(group.namespace) ?? group.title,
          entries: [...group.entries],
        })
    }
    return [...groups.values()].sort(
      (a, b) =>
        a.title.localeCompare(b.title) || a.namespace.localeCompare(b.namespace)
    )
  }, [index, registryActions])
  const [open, setOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const selected = new Set(actions)
  const saved = new Set(savedActions)
  const approvals = new Set(
    rules.filter((rule) => rule.allow).map((rule) => rule.tool)
  )
  const blocked = getBlockedActions(actions, namespaces)
  const loading = registryLoading || mcpLoading
  const ready = !loading && !toolsLoadError
  const skillKeys = new Set(
    effectiveActions?.filter((key) => !saved.has(key) && !selected.has(key))
  )
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
    const integration = index.byKey.get(`mcp:${id}`)?.integration
    return matches(integration?.name ?? id, integration?.slug ?? "")
  })
  const hasVisibleRegistry = registryGroups.some((group) =>
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

  function writeRules(next: PresetToolFields["toolApprovals"]) {
    if (next !== getValues("toolApprovals"))
      setValue("toolApprovals", next, { shouldDirty: true })
  }

  function changeApproval(key: string, ask: boolean) {
    writeRules(setToolApproval(getValues("toolApprovals"), key, ask))
  }

  function remove(keys: string[]) {
    const current = getValues()
    const next = removeTools(current, new Set(keys))
    if (next.actions !== current.actions)
      setValue("actions", next.actions, { shouldDirty: true })
    writeRules(next.toolApprovals)
  }

  function groupApproval(keys: string[], ask: boolean) {
    let next = getValues("toolApprovals")
    for (const key of keys) next = setToolApproval(next, key, ask)
    writeRules(next)
  }

  function removeIntegration(id: string) {
    const current = getValues("mcpIntegrations")
    if (current.includes(id))
      setValue(
        "mcpIntegrations",
        current.filter((key) => key !== id),
        { shouldDirty: true }
      )
  }

  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-medium">Tools</h3>
        <span className="text-xs text-muted-foreground">
          {actions.filter((key) => index.byKey.has(key)).length}
        </span>
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
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className={cn("size-7 shadow-none", FOCUS)}
              aria-label="Add tools"
              disabled={isSaving || !ready}
              onClick={() => setOpen(true)}
            >
              <Plus className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Add tools</TooltipContent>
        </Tooltip>
      </div>
      {searchOpen && (
        <div className="flex items-center gap-2 px-2">
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
            onClick={() => setValue("namespaces", [], { shouldDirty: true })}
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
          {registryGroups.map((group) => {
            const allowed = group.entries.filter((entry) =>
              selected.has(entry.key)
            )
            const visible = allowed.filter((entry) =>
              matches(entry.title, entry.key, group.title)
            )
            if (!visible.length) return null
            const keys = allowed.map((entry) => entry.key)
            const approvalCount = keys.filter((key) =>
              approvals.has(key)
            ).length
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
                      {getIcon(group.entries[0].key, {
                        className: "size-6 shrink-0 rounded border",
                      })}
                      <span className="min-w-0 shrink-[1] truncate text-xs font-medium">
                        {group.title}
                      </span>
                      <span className="min-w-0 shrink-[100] truncate font-mono text-[10px] text-muted-foreground">
                        {group.namespace}
                      </span>
                      <div className="ml-auto flex shrink-0 items-center gap-1">
                        {approvalCount > 0 && (
                          <Badge
                            variant="secondary"
                            className={cn(COUNT_BADGE, APPROVAL_COLOR)}
                          >
                            {approvalCount} need approval
                          </Badge>
                        )}
                        <Badge variant="secondary" className={COUNT_BADGE}>
                          {allowed.length} of {group.entries.length}
                        </Badge>
                      </div>
                    </button>
                  </ContextMenuTrigger>
                  <ContextMenuContent className="w-52 shadow-none">
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
                    <ActionRow
                      key={entry.key}
                      tool={entry.key}
                      title={entry.title}
                      ask={approvals.has(entry.key)}
                      disabled={isSaving}
                      onApproval={() =>
                        changeApproval(entry.key, !approvals.has(entry.key))
                      }
                      onRemove={() => remove([entry.key])}
                    />
                  ))}
              </div>
            )
          })}
          {visibleIntegrations.map((id) => {
            const integration = index.byKey.get(`mcp:${id}`)?.integration
            if (!matches(integration?.name ?? id, integration?.slug ?? ""))
              return null
            const approvalCount =
              integration?.server_type !== "stdio"
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
                <ActionRow
                  key={key}
                  tool={key}
                  title={index.byKey.get(key)?.title}
                  showIcon
                  ask={approvals.has(key)}
                  disabled={isSaving}
                  onApproval={() => changeApproval(key, !approvals.has(key))}
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
                <ActionRow
                  key={key}
                  tool={key}
                  disabled={isSaving}
                  onRemove={() => remove([key])}
                />
              ))}
            </div>
          )}
          {visibleOtherRules.length > 0 && (
            <div>
              <h4 className="py-3 text-xs text-muted-foreground">
                Other approval rules
              </h4>
              {visibleOtherRules.map((rule) => (
                <ActionRow
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
      <AgentToolPickerDialog
        index={index}
        open={open && ready}
        onOpenChange={setOpen}
        maxTools={maxTools}
        disabled={isSaving}
      />
    </section>
  )
}

function ActionRow({
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
          <HoverCardContent className="w-64 p-3 text-xs">
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
