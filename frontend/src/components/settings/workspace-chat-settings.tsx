"use client"

import {
  CheckIcon,
  MousePointerClickIcon,
  PlugZapIcon,
  SearchIcon,
  WrenchIcon,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import type { ChatCapabilitySelection, WorkspaceRead } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { isAgentToolSelectable } from "@/lib/agent-tools"
import {
  useBuilderRegistryActions,
  useListMcpIntegrations,
  useWorkspaceSettings,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"

import {
  type ResolvedChatSettings as ChatSettings,
  resolveChatSettings,
} from "@/lib/workspace-chat"

type CapabilitySelection = Required<ChatCapabilitySelection>
type CapabilityKey = keyof ChatSettings
type CapabilityOption = {
  id: string
  name: string
  description: string
  disabledReason?: string
}

const MODES = [
  { value: "all", label: "All" },
  { value: "selected", label: "Selected" },
  { value: "none", label: "None" },
] as const

/** Configure the limits used by default workspace chats. */
export function WorkspaceChatSettings({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const [saved, setSaved] = useState(() =>
    resolveChatSettings(workspace.settings?.chat)
  )
  const [draft, setDraft] = useState(saved)
  const [justSaved, setJustSaved] = useState(false)
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useBuilderRegistryActions()
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspace.id)
  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspace.id
  )

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved)
  const categories = [
    {
      key: "tools" as const,
      title: "Tools",
      description: "Actions for cases, tables, workflows, and integrations.",
      icon: WrenchIcon,
      allDescription: "All available tools, including new tools added later.",
      noneDescription: "No Tracecat actions are available to the default chat.",
      emptyDescription: "No tools available in this workspace.",
      loading: registryActionsIsLoading,
      error: registryActionsError,
      options: (registryActions ?? [])
        .filter((action) => isAgentToolSelectable(action.action))
        .map((action) => ({
          id: action.action,
          name: action.default_title || action.action,
          description: action.action,
        })),
    },
    {
      key: "mcp" as const,
      title: "MCP servers",
      description: "Tools from servers connected to this workspace.",
      icon: PlugZapIcon,
      allDescription: "All connected servers, including servers added later.",
      noneDescription: "The default chat cannot use connected MCP servers.",
      emptyDescription: "Connect an MCP server to make it available here.",
      loading: mcpIntegrationsIsLoading,
      error: mcpIntegrationsError,
      options: (mcpIntegrations ?? []).map((server) => ({
        id: server.id,
        name: server.name,
        description: server.description || "MCP server",
        disabledReason:
          server.state === "connected"
            ? undefined
            : "Connect this server before selecting it.",
      })),
    },
    {
      key: "subagents" as const,
      title: "Subagents",
      description: "Saved agents the default chat can delegate tasks to.",
      icon: MousePointerClickIcon,
      allDescription: "All eligible agents, including agents added later.",
      noneDescription: "The default chat cannot delegate to saved agents.",
      emptyDescription: "Saved agents will appear here when you create them.",
      loading: presetsIsLoading,
      error: presetsError,
      options: (presets ?? []).map((preset) => ({
        id: preset.id,
        name: preset.name,
        description: preset.description || "Saved agent",
        disabledReason:
          preset.current_version_subagent_eligibility?.eligible === false
            ? (preset.current_version_subagent_eligibility.message ??
              "This agent cannot be used as a subagent.")
            : undefined,
      })),
    },
  ]

  function updateCategory(key: CapabilityKey, value: CapabilitySelection) {
    setDraft((current) => ({ ...current, [key]: value }))
    setJustSaved(false)
  }

  async function saveSettings() {
    try {
      const updated = await updateWorkspace({ settings: { chat: draft } })
      const next = resolveChatSettings(updated.settings?.chat)
      setSaved(next)
      setDraft(next)
      setJustSaved(true)
    } catch {
      // The mutation shows the error; retain the draft so the user can retry.
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-6">
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-semibold tracking-tight">
            Workspace chat
          </h2>
        </div>
        <p className="max-w-xl text-sm leading-6 text-muted-foreground">
          Choose what chat can use when no agent preset is selected. These
          settings apply across {workspace.name}.
        </p>
      </div>

      <div className="divide-y border-y">
        {categories.map(({ key, ...category }) => (
          <CapabilityRow
            key={key}
            {...category}
            categoryKey={key}
            value={draft[key]}
            disabled={isUpdating}
            onChange={(value) => updateCategory(key, value)}
          />
        ))}
      </div>

      <div className="space-y-2 text-xs leading-5 text-muted-foreground">
        <p>
          General-purpose helpers remain available and use the same permissions
          as the default chat.
        </p>
        <p>
          Saved subagents use their own tools. Case chats and chats with a
          selected agent preset are unchanged.
        </p>
      </div>

      <div className="mt-auto flex flex-wrap items-center justify-between gap-4 border-t pt-5">
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {justSaved ? (
            <span className="inline-flex items-center gap-1.5">
              <CheckIcon className="size-3.5" /> Saved
            </span>
          ) : (
            "Changes apply on the next message in default workspace chats."
          )}
        </p>
        <div className="flex items-center gap-2">
          {dirty && (
            <Button variant="ghost" size="sm" onClick={() => setDraft(saved)}>
              Discard
            </Button>
          )}
          <Button
            size="sm"
            onClick={saveSettings}
            disabled={isUpdating || !dirty}
          >
            Save changes
          </Button>
        </div>
      </div>
    </div>
  )
}

function CapabilityRow({
  categoryKey,
  title,
  description,
  icon: Icon,
  allDescription,
  noneDescription,
  emptyDescription,
  loading,
  error,
  options,
  value,
  disabled,
  onChange,
}: {
  categoryKey: CapabilityKey
  title: string
  description: string
  icon: typeof WrenchIcon
  allDescription: string
  noneDescription: string
  emptyDescription: string
  loading: boolean
  error: unknown
  options: CapabilityOption[]
  value: CapabilitySelection
  disabled: boolean
  onChange: (value: CapabilitySelection) => void
}) {
  const [query, setQuery] = useState("")
  const filtered = options.filter((option) =>
    `${option.name} ${option.description}`
      .toLowerCase()
      .includes(query.toLowerCase())
  )
  const available = options.filter((option) => !option.disabledReason)
  const count = available.filter((option) =>
    value.selected.includes(option.id)
  ).length

  function toggle(id: string) {
    const selected = value.selected.includes(id)
      ? value.selected.filter((item) => item !== id)
      : [...value.selected, id]
    onChange({ ...value, selected })
  }

  let listContent: ReactNode
  if (loading) {
    listContent = (
      <p className="px-3 py-6 text-center text-xs text-muted-foreground">
        Loading {title.toLowerCase()}…
      </p>
    )
  } else if (error) {
    listContent = (
      <p className="px-3 py-6 text-center text-xs text-destructive">
        Could not load {title.toLowerCase()}. Reopen settings to try again.
      </p>
    )
  } else if (filtered.length === 0) {
    listContent = (
      <p className="px-3 py-6 text-center text-xs text-muted-foreground">
        {query ? "No matches. Try a different search." : emptyDescription}
      </p>
    )
  } else {
    listContent = filtered.slice(0, 60).map((option) => (
      <label
        key={option.id}
        className={cn(
          "flex items-start gap-3 rounded px-2 py-2.5 hover:bg-muted/50",
          option.disabledReason
            ? "cursor-not-allowed opacity-50"
            : "cursor-pointer"
        )}
      >
        <Checkbox
          checked={value.selected.includes(option.id)}
          disabled={disabled || Boolean(option.disabledReason)}
          onCheckedChange={() => toggle(option.id)}
          className="mt-0.5"
        />
        <span className="min-w-0 space-y-0.5">
          <span className="block text-xs font-medium">{option.name}</span>
          <span
            className="block truncate text-[11px] text-muted-foreground"
            title={option.disabledReason || option.description}
          >
            {option.disabledReason || option.description}
          </span>
        </span>
      </label>
    ))
  }
  const modeDescription =
    value.mode === "all" ? allDescription : noneDescription

  return (
    <section
      className="space-y-3 py-5"
      aria-labelledby={`${categoryKey}-heading`}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="space-y-1">
            <h3 id={`${categoryKey}-heading`} className="text-sm font-medium">
              {title}
            </h3>
            <p className="max-w-[310px] text-xs leading-5 text-muted-foreground">
              {description}
            </p>
          </div>
        </div>
        <fieldset
          className="flex shrink-0 rounded-md border p-0.5"
          disabled={disabled}
        >
          <legend className="sr-only">{title} access</legend>
          {MODES.map((mode) => (
            <label key={mode.value} className="relative cursor-pointer">
              <input
                type="radio"
                name={`${categoryKey}-mode`}
                value={mode.value}
                checked={value.mode === mode.value}
                onChange={() => onChange({ ...value, mode: mode.value })}
                className="peer sr-only"
              />
              <span className="block min-w-14 rounded px-3 py-1 text-center text-xs text-muted-foreground transition-colors hover:text-foreground peer-checked:bg-muted peer-checked:font-medium peer-checked:text-foreground peer-focus-visible:ring-1 peer-focus-visible:ring-inset peer-focus-visible:ring-ring">
                {mode.label}
              </span>
            </label>
          ))}
        </fieldset>
      </div>

      {value.mode !== "selected" ? (
        <p className="pl-7 text-xs text-muted-foreground">{modeDescription}</p>
      ) : (
        <div className="space-y-2 pl-7">
          <div className="overflow-hidden rounded-md border">
            <div className="relative border-b">
              <SearchIcon className="pointer-events-none absolute left-3 top-3 size-3.5 text-muted-foreground" />
              <Input
                aria-label={`Search ${title.toLowerCase()}`}
                placeholder={`Search ${title.toLowerCase()}…`}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="h-10 rounded-none border-0 pl-9 text-xs shadow-none"
              />
            </div>
            <div className="flex items-center justify-between gap-3 border-b px-3 py-2 text-xs text-muted-foreground">
              <span>
                {count} of {available.length} selected
              </span>
              <Button
                variant="link"
                size="sm"
                className="h-auto p-0 text-xs text-muted-foreground"
                disabled={
                  disabled ||
                  loading ||
                  Boolean(error) ||
                  available.length === 0
                }
                onClick={() =>
                  onChange({
                    ...value,
                    selected:
                      count === available.length
                        ? []
                        : available.map((option) => option.id),
                  })
                }
              >
                {count === available.length && count > 0
                  ? "Clear selection"
                  : "Select all current"}
              </Button>
            </div>
            <div className="max-h-60 overflow-y-auto p-1">
              {listContent}
              {filtered.length > 60 && (
                <p className="px-3 py-3 text-xs text-muted-foreground">
                  Showing 60 of {filtered.length}. Search to narrow the list.
                </p>
              )}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            New items stay unavailable until you select them.
          </p>
        </div>
      )}
    </section>
  )
}
