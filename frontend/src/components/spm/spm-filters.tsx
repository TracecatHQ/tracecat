"use client"

import { ChevronDownIcon, XCircleIcon } from "lucide-react"
import type { ComponentType } from "react"
import type {
  SpmAssetClass,
  SpmAssetType,
  SpmEndpointRead,
  SpmEndpointStatus,
  SpmFindingStatus,
  SpmHarness,
  SpmSeverity,
} from "@/client"
import { cn } from "@/lib/utils"
import { ALL_VALUE } from "./spm-common"

export type FilterOption<TValue extends string = string> = {
  value: TValue
  label: string
}

export const SEVERITY_OPTIONS: Array<
  FilterOption<SpmSeverity | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "All severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
]

export const ASSET_CLASS_OPTIONS: Array<
  FilterOption<SpmAssetClass | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "All classes" },
  { value: "mcp_server", label: "MCP server" },
  { value: "skill", label: "Skill" },
  { value: "instruction_file", label: "Instruction file" },
  { value: "workspace_access", label: "Workspace access" },
  { value: "permissions", label: "Permissions" },
  { value: "sandbox", label: "Sandbox" },
  { value: "extension", label: "Extension" },
  { value: "agent", label: "Agent" },
]

export const ASSET_TYPE_OPTIONS: Array<
  FilterOption<SpmAssetType | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "All types" },
  { value: "mcp_server", label: "MCP server" },
  { value: "skill", label: "Skill" },
  { value: "claude_md", label: "CLAUDE.md" },
  { value: "agents_md", label: "AGENTS.md" },
  { value: "trusted_directory", label: "Trusted directory" },
  { value: "additional_directory", label: "Additional directory" },
  { value: "permission_config", label: "Permission config" },
  { value: "sandbox_config", label: "Sandbox config" },
  { value: "hook", label: "Hook" },
  { value: "subagent", label: "Subagent" },
]

export const ENDPOINT_STATUS_OPTIONS: Array<
  FilterOption<SpmEndpointStatus | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "All endpoint status" },
  { value: "active", label: "Active" },
  { value: "pending", label: "Pending" },
  { value: "error", label: "Error" },
  { value: "disabled", label: "Disabled" },
]

export const FINDING_STATUS_OPTIONS: Array<
  FilterOption<SpmFindingStatus | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "All finding status" },
  { value: "open", label: "Open" },
  { value: "enforcement_pending", label: "Enforcement pending" },
  { value: "enforced", label: "Enforced" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
]

export const COMPLIANCE_OPTIONS = [
  { value: ALL_VALUE, label: "All compliance" },
  { value: "needs_attention", label: "Needs attention" },
  { value: "enforcement_queued", label: "Enforcement queued" },
  { value: "compliant", label: "Compliant" },
  { value: "unknown", label: "Unknown" },
] as const

export const SYNC_OPTIONS = [
  { value: ALL_VALUE, label: "All sync states" },
  { value: "healthy", label: "Healthy sync" },
  { value: "error", label: "Sync error" },
] as const

export const HARNESS_OPTIONS: Array<
  FilterOption<SpmHarness | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "All harnesses" },
  { value: "claude_code", label: "Claude Code" },
]

export function endpointOptions(endpoints: SpmEndpointRead[]) {
  return [
    { value: ALL_VALUE, label: "All endpoints" },
    ...endpoints.map((endpoint) => ({
      value: endpoint.id,
      label: endpoint.name,
    })),
  ]
}

export function controlOptions(controls: Array<{ id: string; title: string }>) {
  return [
    { value: ALL_VALUE, label: "All controls" },
    ...controls.map((control) => ({
      value: control.id,
      label: control.title,
    })),
  ]
}

export function FilterSelect<TValue extends string>(props: {
  icon: ComponentType<{ className?: string }>
  label: string
  onChange: (value: TValue) => void
  options: Array<FilterOption<TValue>>
  value: TValue
}) {
  const Icon = props.icon
  const isFiltered = props.value !== ALL_VALUE

  return (
    <label
      className={cn(
        "inline-flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors hover:bg-muted/50",
        isFiltered && "border-primary/50 bg-primary/5"
      )}
    >
      <Icon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="text-muted-foreground">{props.label}</span>
      <select
        aria-label={`Filter by ${props.label.toLowerCase()}`}
        className="max-w-[180px] appearance-none bg-transparent pr-4 text-xs font-medium text-foreground outline-none"
        value={props.value}
        onChange={(event) => props.onChange(event.target.value as TValue)}
      >
        {props.options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDownIcon className="-ml-4 size-3 shrink-0 text-muted-foreground" />
    </label>
  )
}

export function ResetFiltersButton(props: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className="flex h-6 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      Reset
      <XCircleIcon className="size-3" />
    </button>
  )
}
