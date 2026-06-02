"use client"

import fuzzysort from "fuzzysort"
import {
  ArrowRightIcon,
  ChevronRightIcon,
  PlugZapIcon,
  SearchIcon,
  SlidersHorizontalIcon,
} from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import type { MCPIntegrationRead, RegistryActionReadMinimal } from "@/client"
import { PromptInputButton } from "@/components/ai-elements/prompt-input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
import type { ChatSurface } from "@/types/chat-surface"

interface ChatToolsPickerProps {
  registryActions: RegistryActionReadMinimal[]
  selectedTools: string[]
  onToolsChange: (next: string[]) => void
  mcpIntegrations: MCPIntegrationRead[]
  selectedMcpIntegrations: string[]
  onMcpChange: (next: string[]) => void
  agentAddonsEnabled?: boolean
  mcpEnabled?: boolean
  disabled?: boolean
  /** Selects which chat surface-specific default capabilities are read-only. */
  surface?: ChatSurface
  /**
   * Link to the workspace MCP servers page. When provided, the empty MCP
   * state becomes a CTA pointing there.
   */
  mcpIntegrationsHref?: string
}

type ToolOption = {
  value: string
  label: string
  group: string
  description: string
  stale?: boolean
}

type McpOption = {
  id: string
  name: string
  description?: string
  stale?: boolean
}

type CapabilityGroup = {
  id: string
  label: string
  tools: string[]
  addon?: boolean
}

const TOOL_SEARCH_LIMIT = 24

/**
 * Always-on workspace chat capabilities, grouped for display. Mirrors
 * `WORKSPACE_CHAT_DEFAULT_TOOLS` in `tracecat/chat/tools.py` — keep in sync when
 * the backend default tool set changes. These are surfaced read-only so users
 * can see what the agent can already do before adding anything.
 */
const DEFAULT_CAPABILITY_GROUPS: CapabilityGroup[] = [
  {
    id: "cases",
    label: "Cases",
    tools: [
      "core.cases.create_case",
      "core.cases.update_case",
      "core.cases.delete_case",
      "core.cases.list_cases",
      "core.cases.get_case",
      "core.cases.search_cases",
    ],
  },
  {
    id: "tables",
    label: "Tables",
    tools: [
      "core.table.list_tables",
      "core.table.get_table_metadata",
      "core.table.create_table",
      "core.table.update_table",
      "core.table.create_column",
      "core.table.update_column",
      "core.table.delete_column",
      "core.table.lookup",
      "core.table.lookup_many",
      "core.table.is_in",
      "core.table.search_rows",
      "core.table.insert_row",
      "core.table.insert_rows",
      "core.table.update_row",
      "core.table.delete_row",
      "core.table.download",
    ],
  },
  {
    id: "workflows",
    label: "Workflows",
    tools: ["core.workflow.create_workflow"],
  },
  {
    id: "presets",
    label: "Agent presets",
    addon: true,
    tools: [
      "ai.agent.create_preset",
      "ai.agent.get_preset",
      "ai.agent.list_presets",
      "ai.agent.update_preset",
    ],
  },
]

const DEFAULT_TOOL_VALUES = new Set(
  DEFAULT_CAPABILITY_GROUPS.flatMap((group) => group.tools)
)
const EMPTY_DEFAULT_TOOL_VALUES = new Set<string>()

/** Humanize an action id (e.g. `core.table.list_tables` -> `List tables`). */
function humanizeAction(action: string): string {
  const last = action.split(".").pop() ?? action
  const spaced = last.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** "1 tool" / "4 tools" */
function toolCountLabel(count: number): string {
  return `${count} tool${count === 1 ? "" : "s"}`
}

/**
 * Unified picker for inspecting always-on capabilities and attaching extra
 * registry tools and MCP integrations to a chat session. Tracecat platform
 * defaults are always on and merged at runtime; this control surfaces them
 * read-only and manages additions on top of that baseline.
 */
export function ChatToolsPicker({
  registryActions,
  selectedTools,
  onToolsChange,
  mcpIntegrations,
  selectedMcpIntegrations,
  onMcpChange,
  agentAddonsEnabled = true,
  mcpEnabled = true,
  disabled = false,
  surface = "regular",
  mcpIntegrationsHref,
}: ChatToolsPickerProps) {
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const isWorkspaceChat = surface === "workspace-chat"
  const defaultToolValues = isWorkspaceChat
    ? DEFAULT_TOOL_VALUES
    : EMPTY_DEFAULT_TOOL_VALUES

  const addedCount =
    selectedTools.length + (mcpEnabled ? selectedMcpIntegrations.length : 0)

  const toggleExpanded = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const toolOptions = useMemo<ToolOption[]>(
    () =>
      registryActions
        .map((action) => ({
          value: action.action,
          label: action.default_title || action.action,
          group: action.display_group || action.namespace,
          description: action.description,
        }))
        .sort((a, b) => a.value.localeCompare(b.value)),
    [registryActions]
  )

  const optionByValue = useMemo(
    () => new Map(toolOptions.map((option) => [option.value, option])),
    [toolOptions]
  )

  const visibleCapabilityGroups = useMemo(
    () =>
      isWorkspaceChat
        ? DEFAULT_CAPABILITY_GROUPS.filter(
            (group) => !group.addon || agentAddonsEnabled
          )
        : [],
    [agentAddonsEnabled, isWorkspaceChat]
  )

  // Tools the user can add: everything except this surface's always-on defaults.
  const addableTools = useMemo<ToolOption[]>(
    () => toolOptions.filter((option) => !defaultToolValues.has(option.value)),
    [defaultToolValues, toolOptions]
  )

  // Group addable tools by their integration (display group) for browsing.
  const addableGroups = useMemo<
    { group: string; tools: ToolOption[] }[]
  >(() => {
    const byGroup = new Map<string, ToolOption[]>()
    for (const tool of addableTools) {
      const list = byGroup.get(tool.group)
      if (list) {
        list.push(tool)
      } else {
        byGroup.set(tool.group, [tool])
      }
    }
    return Array.from(byGroup.entries())
      .map(([group, tools]) => ({ group, tools }))
      .sort((a, b) => a.group.localeCompare(b.group))
  }, [addableTools])

  // Default tools should not be persisted as extras, but older sessions or the
  // mention menu can still put them in selectedTools. Surface them as removable.
  const selectedDefaultTools = useMemo<ToolOption[]>(
    () =>
      selectedTools
        .filter((value) => defaultToolValues.has(value))
        .map((value) => {
          const option = optionByValue.get(value)
          if (option) {
            return {
              ...option,
              group: "Included by default",
            }
          }
          return {
            value,
            label: humanizeAction(value),
            group: "Included by default",
            description: "This tool is already included by default.",
          }
        }),
    [defaultToolValues, optionByValue, selectedTools]
  )

  // Selected tools that are no longer in the registry, surfaced so they can be
  // removed even when the user is not searching.
  const staleSelectedTools = useMemo<ToolOption[]>(
    () =>
      selectedTools
        .filter(
          (value) => !defaultToolValues.has(value) && !optionByValue.has(value)
        )
        .map((value) => ({
          value,
          label: value,
          group: "No longer available",
          description: "This tool is no longer available.",
          stale: true,
        })),
    [defaultToolValues, optionByValue, selectedTools]
  )

  const searchResults = useMemo<ToolOption[]>(() => {
    const needle = query.trim()
    if (!needle) {
      return []
    }
    return fuzzysort
      .go<ToolOption>(
        needle,
        [...addableTools, ...selectedDefaultTools, ...staleSelectedTools],
        {
          keys: ["value", "label", "description", "group"],
          limit: TOOL_SEARCH_LIMIT,
        }
      )
      .map((result) => result.obj)
  }, [query, addableTools, selectedDefaultTools, staleSelectedTools])

  const mcpOptionById = useMemo(
    () =>
      new Map(
        mcpIntegrations.map((integration) => [integration.id, integration])
      ),
    [mcpIntegrations]
  )

  const visibleMcpIntegrations = useMemo<McpOption[]>(
    () => [
      ...mcpIntegrations.map((integration) => ({
        id: integration.id,
        name: integration.name,
        description: integration.description ?? undefined,
      })),
      ...selectedMcpIntegrations
        .filter((id) => !mcpOptionById.has(id))
        .map((id) => ({
          id,
          name: id,
          description: "This MCP integration is no longer available.",
          stale: true,
        })),
    ],
    [mcpIntegrations, mcpOptionById, selectedMcpIntegrations]
  )

  const toggleTool = (value: string) => {
    if (selectedTools.includes(value)) {
      onToolsChange(selectedTools.filter((tool) => tool !== value))
      return
    }
    onToolsChange([...selectedTools, value])
  }

  const toggleGroup = (tools: ToolOption[]) => {
    const values = tools.map((tool) => tool.value)
    const allSelected = values.every((value) => selectedTools.includes(value))
    if (allSelected) {
      const remove = new Set(values)
      onToolsChange(selectedTools.filter((tool) => !remove.has(tool)))
      return
    }
    const next = new Set(selectedTools)
    for (const value of values) {
      next.add(value)
    }
    onToolsChange(Array.from(next))
  }

  const toggleMcp = (id: string) => {
    if (selectedMcpIntegrations.includes(id)) {
      onMcpChange(selectedMcpIntegrations.filter((value) => value !== id))
      return
    }
    onMcpChange([...selectedMcpIntegrations, id])
  }

  const isSearching = query.trim().length > 0

  return (
    <Popover>
      <PopoverTrigger asChild>
        <PromptInputButton disabled={disabled} className="text-xs">
          <SlidersHorizontalIcon className="size-4" />
          <span>{addedCount > 0 ? `Tools (${addedCount})` : "Tools"}</span>
        </PromptInputButton>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-80 p-0 font-normal text-[13px]"
      >
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search capabilities & tools..."
            className="w-full bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="max-h-80 overflow-y-auto py-1.5">
          {isSearching ? (
            <>
              <GroupLabel>Add tools</GroupLabel>
              {searchResults.length > 0 ? (
                searchResults.map((tool) => (
                  <Row
                    key={tool.value}
                    dotClassName={
                      tool.stale ? "bg-muted-foreground" : "bg-amber-500"
                    }
                    title={tool.label}
                    subtitle={tool.group}
                    checked={selectedTools.includes(tool.value)}
                    onToggle={() => toggleTool(tool.value)}
                  />
                ))
              ) : (
                <EmptyHint>No tools found.</EmptyHint>
              )}
            </>
          ) : (
            <>
              {isWorkspaceChat ? (
                <>
                  <GroupLabel pill="Always on">Active capabilities</GroupLabel>
                  {visibleCapabilityGroups.map((group) => (
                    <CapabilityRow
                      key={group.id}
                      group={group}
                      optionByValue={optionByValue}
                      open={expanded.has(`cap:${group.id}`)}
                      onToggle={() => toggleExpanded(`cap:${group.id}`)}
                    />
                  ))}
                </>
              ) : null}

              {mcpEnabled && (
                <>
                  <GroupLabel>MCP integrations</GroupLabel>
                  {visibleMcpIntegrations.length > 0 ? (
                    visibleMcpIntegrations.map((integration) => (
                      <Row
                        key={integration.id}
                        dotClassName={
                          integration.stale
                            ? "bg-muted-foreground"
                            : "bg-sky-500"
                        }
                        title={integration.name}
                        subtitle={integration.description}
                        checked={selectedMcpIntegrations.includes(
                          integration.id
                        )}
                        onToggle={() => toggleMcp(integration.id)}
                      />
                    ))
                  ) : mcpIntegrationsHref ? (
                    <Link
                      href={mcpIntegrationsHref}
                      className="mx-2.5 my-1 flex items-center gap-3 rounded-md border border-dashed px-3 py-2.5 text-foreground hover:bg-muted"
                    >
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                        <PlugZapIcon className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium">
                          Connect an MCP server
                        </div>
                        <p className="truncate text-[11px] text-muted-foreground">
                          Bring your own tools into chat.
                        </p>
                      </div>
                      <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    </Link>
                  ) : (
                    <EmptyHint>
                      No MCP integrations connected in this workspace.
                    </EmptyHint>
                  )}
                </>
              )}

              <GroupLabel>Add tools</GroupLabel>
              {selectedDefaultTools.map((tool) => (
                <Row
                  key={tool.value}
                  dotClassName="bg-emerald-500"
                  title={tool.label}
                  subtitle={tool.group}
                  checked
                  onToggle={() => toggleTool(tool.value)}
                />
              ))}
              {staleSelectedTools.map((tool) => (
                <Row
                  key={tool.value}
                  dotClassName="bg-muted-foreground"
                  title={tool.label}
                  subtitle={tool.group}
                  checked
                  onToggle={() => toggleTool(tool.value)}
                />
              ))}
              {addableGroups.length > 0 ? (
                addableGroups.map(({ group, tools }) => {
                  const values = tools.map((tool) => tool.value)
                  const selectedInGroup = values.filter((value) =>
                    selectedTools.includes(value)
                  ).length
                  return (
                    <AddGroupRow
                      key={group}
                      group={group}
                      tools={tools}
                      selectedCount={selectedInGroup}
                      allSelected={selectedInGroup === values.length}
                      open={expanded.has(`add:${group}`)}
                      onToggleOpen={() => toggleExpanded(`add:${group}`)}
                      onToggleGroup={() => toggleGroup(tools)}
                      onToggleTool={toggleTool}
                      selectedTools={selectedTools}
                    />
                  )
                })
              ) : staleSelectedTools.length === 0 &&
                selectedDefaultTools.length === 0 ? (
                <EmptyHint>No registry tools available.</EmptyHint>
              ) : null}
            </>
          )}
        </div>

        <div className="border-t px-3 py-2 text-[11px] text-muted-foreground">
          {addedCount} added
          {isWorkspaceChat ? " · Tracecat defaults always included" : ""}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function GroupLabel({
  children,
  pill,
}: {
  children: React.ReactNode
  pill?: string
}) {
  return (
    <div className="flex items-center gap-2 px-3 pb-1 pt-2">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {children}
      </span>
      {pill ? (
        <span className="rounded border px-1 py-0 text-[9px] leading-4 text-muted-foreground">
          {pill}
        </span>
      ) : null}
    </div>
  )
}

function Dot({ className }: { className?: string }) {
  return <span className={cn("size-2 shrink-0 rounded-full", className)} />
}

function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronRightIcon
      className={cn(
        "size-3.5 shrink-0 text-muted-foreground transition-transform",
        open && "rotate-90"
      )}
    />
  )
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-1.5 text-xs text-muted-foreground">{children}</p>
}

function Row({
  dotClassName,
  title,
  subtitle,
  checked,
  onToggle,
}: {
  dotClassName: string
  title: string
  subtitle?: string
  checked: boolean
  onToggle: () => void
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 hover:bg-muted">
      <Dot className={dotClassName} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] text-foreground/70">{title}</div>
        {subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      <Switch checked={checked} onCheckedChange={onToggle} />
    </label>
  )
}

/** Collapsible always-on capability group (read-only). */
function CapabilityRow({
  group,
  optionByValue,
  open,
  onToggle,
}: {
  group: CapabilityGroup
  optionByValue: Map<string, ToolOption>
  open: boolean
  onToggle: () => void
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left hover:bg-muted"
      >
        <Chevron open={open} />
        <Dot className="bg-emerald-500" />
        <span className="flex-1 truncate text-[13px] text-foreground/70">
          {group.label}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {toolCountLabel(group.tools.length)}
          {group.addon ? " · add-on" : ""}
        </span>
      </button>
      {open ? (
        <div className="bg-muted/30">
          {group.tools.map((value) => (
            <div
              key={value}
              className="flex items-center gap-2.5 py-1 pl-9 pr-3"
            >
              <span className="size-1.5 shrink-0 rounded-full bg-emerald-500/50" />
              <span className="flex-1 truncate text-[13px] text-foreground/70">
                {optionByValue.get(value)?.label ?? humanizeAction(value)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** Collapsible integration group of addable tools with a group-level toggle. */
function AddGroupRow({
  group,
  tools,
  selectedCount,
  allSelected,
  open,
  onToggleOpen,
  onToggleGroup,
  onToggleTool,
  selectedTools,
}: {
  group: string
  tools: ToolOption[]
  selectedCount: number
  allSelected: boolean
  open: boolean
  onToggleOpen: () => void
  onToggleGroup: () => void
  onToggleTool: (value: string) => void
  selectedTools: string[]
}) {
  return (
    <div>
      <div className="flex items-center gap-2.5 px-3 py-1.5 hover:bg-muted">
        <button
          type="button"
          onClick={onToggleOpen}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
        >
          <Chevron open={open} />
          <Dot className="bg-amber-500" />
          <span className="flex-1 truncate text-[13px] text-foreground/70">
            {group}
          </span>
          <span className="text-[11px] text-muted-foreground">
            {selectedCount > 0
              ? `${selectedCount}/${tools.length} on`
              : toolCountLabel(tools.length)}
          </span>
        </button>
        <Switch checked={allSelected} onCheckedChange={onToggleGroup} />
      </div>
      {open ? (
        <div className="bg-muted/30">
          {tools.map((tool) => (
            <label
              key={tool.value}
              className="flex cursor-pointer items-center gap-2.5 py-1 pl-9 pr-3 hover:bg-muted"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] text-foreground/70">
                  {tool.label}
                </div>
                {tool.description ? (
                  <p className="truncate text-[11px] text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </div>
              <Switch
                checked={selectedTools.includes(tool.value)}
                onCheckedChange={() => onToggleTool(tool.value)}
              />
            </label>
          ))}
        </div>
      ) : null}
    </div>
  )
}
