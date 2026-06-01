"use client"

import fuzzysort from "fuzzysort"
import { SearchIcon, SlidersHorizontalIcon } from "lucide-react"
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

interface ChatToolsPickerProps {
  registryActions: RegistryActionReadMinimal[]
  selectedTools: string[]
  onToolsChange: (next: string[]) => void
  mcpIntegrations: MCPIntegrationRead[]
  selectedMcpIntegrations: string[]
  onMcpChange: (next: string[]) => void
  disabled?: boolean
}

type ToolOption = {
  value: string
  label: string
  group: string
  description: string
}

const TOOL_SEARCH_LIMIT = 24

/**
 * Unified picker for attaching extra registry tools and MCP integrations to a
 * chat session. Tracecat platform defaults are always on and merged at runtime;
 * this control only manages additions on top of that baseline.
 */
export function ChatToolsPicker({
  registryActions,
  selectedTools,
  onToolsChange,
  mcpIntegrations,
  selectedMcpIntegrations,
  onMcpChange,
  disabled = false,
}: ChatToolsPickerProps) {
  const [query, setQuery] = useState("")

  const addedCount = selectedTools.length + selectedMcpIntegrations.length

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

  const visibleTools = useMemo<ToolOption[]>(() => {
    const needle = query.trim()
    if (!needle) {
      // No query: surface only the tools already attached so they can be removed.
      return selectedTools
        .map((value) => optionByValue.get(value))
        .filter((option): option is ToolOption => Boolean(option))
    }
    return fuzzysort
      .go<ToolOption>(needle, toolOptions, {
        keys: ["value", "label", "description", "group"],
        limit: TOOL_SEARCH_LIMIT,
      })
      .map((result) => result.obj)
  }, [query, toolOptions, optionByValue, selectedTools])

  const toggleTool = (value: string) => {
    if (selectedTools.includes(value)) {
      onToolsChange(selectedTools.filter((tool) => tool !== value))
      return
    }
    onToolsChange([...selectedTools, value])
  }

  const toggleMcp = (id: string) => {
    if (selectedMcpIntegrations.includes(id)) {
      onMcpChange(selectedMcpIntegrations.filter((value) => value !== id))
      return
    }
    onMcpChange([...selectedMcpIntegrations, id])
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <PromptInputButton
          disabled={disabled}
          tooltip="Add tools and integrations"
          className="text-xs"
        >
          <SlidersHorizontalIcon className="size-4" />
          <span>{addedCount > 0 ? `Tools (${addedCount})` : "Tools"}</span>
        </PromptInputButton>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search tools & integrations..."
            className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="max-h-80 overflow-y-auto py-1.5">
          <GroupLabel>Sources</GroupLabel>
          <div className="flex items-center gap-2.5 px-3 py-1.5">
            <Dot className="bg-emerald-500" />
            <span className="flex-1 text-sm">Tracecat platform</span>
            <span className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
              Always on
            </span>
          </div>

          <GroupLabel>MCP integrations</GroupLabel>
          {mcpIntegrations.length > 0 ? (
            mcpIntegrations.map((integration) => (
              <Row
                key={integration.id}
                dotClassName="bg-sky-500"
                title={integration.name}
                subtitle={integration.description ?? undefined}
                checked={selectedMcpIntegrations.includes(integration.id)}
                onToggle={() => toggleMcp(integration.id)}
              />
            ))
          ) : (
            <EmptyHint>
              No MCP integrations connected in this workspace.
            </EmptyHint>
          )}

          <GroupLabel>Tools</GroupLabel>
          {visibleTools.length > 0 ? (
            visibleTools.map((tool) => (
              <Row
                key={tool.value}
                dotClassName="bg-amber-500"
                title={tool.label}
                subtitle={tool.group}
                checked={selectedTools.includes(tool.value)}
                onToggle={() => toggleTool(tool.value)}
              />
            ))
          ) : (
            <EmptyHint>
              {query.trim()
                ? "No tools found."
                : "Search to add registry tools."}
            </EmptyHint>
          )}
        </div>

        <div className="border-t px-3 py-2 text-[11px] text-muted-foreground">
          {addedCount} added · Tracecat defaults always included
        </div>
      </PopoverContent>
    </Popover>
  )
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  )
}

function Dot({ className }: { className?: string }) {
  return <span className={cn("size-2 shrink-0 rounded-full", className)} />
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
        <div className="truncate text-sm">{title}</div>
        {subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      <Switch checked={checked} onCheckedChange={onToggle} />
    </label>
  )
}
