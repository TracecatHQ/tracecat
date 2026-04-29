"use client"

import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircleIcon,
  CircleHelpIcon,
  CirclePauseIcon,
  ClockArrowUpIcon,
  HourglassIcon,
  type LucideIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  XCircleIcon,
} from "lucide-react"
import type { ComponentType } from "react"
import type {
  SpmEndpointRead,
  SpmEndpointStatus,
  SpmFindingStatus,
  SpmHarness,
  SpmInventoryItemType,
  SpmInventorySourceType,
  SpmInventoryTaxonomyRead,
  SpmSeverity,
} from "@/client"
import {
  type FilterMode,
  FilterMultiSelect,
  type FilterOption as MultiFilterOption,
  type SortDirection,
} from "@/components/filters/filter-multi-select"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { ALL_VALUE } from "./spm-common"
import {
  ITEM_TYPE_ICONS,
  itemTypeLabel,
  SOURCE_TYPE_ICONS,
  sourceTypeLabel,
} from "./spm-icons"

export type FilterOption<TValue extends string = string> = {
  value: TValue
  label: string
  icon?: LucideIcon
  iconClassName?: string
}

export type StatusStyle = {
  icon: LucideIcon
  iconClassName: string
  badgeClassName: string
  label: string
}

export const endpointStatusStyles: Record<SpmEndpointStatus, StatusStyle> = {
  active: {
    label: "active",
    icon: CheckCircleIcon,
    iconClassName: "text-green-600",
    badgeClassName: "bg-green-500/10 text-green-700",
  },
  pending: {
    label: "pending",
    icon: HourglassIcon,
    iconClassName: "text-yellow-600",
    badgeClassName: "bg-yellow-500/10 text-yellow-700",
  },
  error: {
    label: "error",
    icon: AlertTriangleIcon,
    iconClassName: "text-red-600",
    badgeClassName: "bg-red-500/10 text-red-700",
  },
  disabled: {
    label: "disabled",
    icon: CirclePauseIcon,
    iconClassName: "text-slate-500",
    badgeClassName: "bg-slate-500/10 text-slate-700",
  },
}

export type EndpointComplianceKey =
  | "compliant"
  | "needs_attention"
  | "enforcement_queued"
  | "unknown"

export const endpointComplianceStyles: Record<
  EndpointComplianceKey,
  StatusStyle
> = {
  compliant: {
    label: "compliant",
    icon: ShieldCheckIcon,
    iconClassName: "text-green-600",
    badgeClassName: "bg-green-500/10 text-green-700",
  },
  needs_attention: {
    label: "needs_attention",
    icon: ShieldAlertIcon,
    iconClassName: "text-yellow-600",
    badgeClassName: "bg-yellow-500/10 text-yellow-700",
  },
  enforcement_queued: {
    label: "enforcement_queued",
    icon: ClockArrowUpIcon,
    iconClassName: "text-blue-600",
    badgeClassName: "bg-blue-500/10 text-blue-700",
  },
  unknown: {
    label: "unknown",
    icon: CircleHelpIcon,
    iconClassName: "text-slate-500",
    badgeClassName: "bg-slate-500/10 text-slate-700",
  },
}

export const SEVERITY_OPTIONS: Array<
  FilterOption<SpmSeverity | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_severities" },
  { value: "critical", label: "critical" },
  { value: "high", label: "high" },
  { value: "medium", label: "medium" },
  { value: "low", label: "low" },
]

export const ITEM_TYPE_OPTIONS: Array<
  FilterOption<SpmInventoryItemType | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_item_types" },
  ...(
    [
      "hook",
      "plugin",
      "mcp_server",
      "instruction_file",
      "permission_config",
      "sandbox_config",
      "trusted_directory",
      "additional_directory",
      "skill",
      "agent",
    ] satisfies SpmInventoryItemType[]
  ).map((itemType) => ({
    value: itemType,
    label: itemTypeLabel(itemType),
    icon: ITEM_TYPE_ICONS[itemType],
  })),
]

export const SOURCE_TYPE_OPTIONS: Array<
  FilterOption<SpmInventorySourceType | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_source_types" },
  ...(
    [
      "settings_json",
      "settings_local_json",
      "claude_json",
      "hooks_json",
      "mcp_json",
      "claude_md",
      "claude_local_md",
      "agents_md",
      "skill_frontmatter",
      "agent_frontmatter",
      "plugin_manifest",
      "directory",
    ] satisfies SpmInventorySourceType[]
  ).map((sourceType) => ({
    value: sourceType,
    label: sourceTypeLabel(sourceType),
    icon: SOURCE_TYPE_ICONS[sourceType],
  })),
]

export function taxonomyItemTypeOptions(
  taxonomy: SpmInventoryTaxonomyRead | undefined,
  harness: SpmHarness = "claude_code"
): Array<FilterOption<SpmInventoryItemType | typeof ALL_VALUE>> {
  const entries = taxonomy?.harnesses[harness]?.item_types
  if (!entries) {
    return ITEM_TYPE_OPTIONS
  }
  return [
    { value: ALL_VALUE, label: "all_item_types" },
    ...entries.map((entry) => {
      const value = entry.key as SpmInventoryItemType
      return {
        value,
        label: entry.display_value,
        icon: ITEM_TYPE_ICONS[value],
      }
    }),
  ]
}

export function taxonomySourceTypeOptions(
  taxonomy: SpmInventoryTaxonomyRead | undefined,
  harness: SpmHarness = "claude_code"
): Array<FilterOption<SpmInventorySourceType | typeof ALL_VALUE>> {
  const entries = taxonomy?.harnesses[harness]?.source_types
  if (!entries) {
    return SOURCE_TYPE_OPTIONS
  }
  return [
    { value: ALL_VALUE, label: "all_source_types" },
    ...entries.map((entry) => {
      const value = entry.key as SpmInventorySourceType
      return {
        value,
        label: entry.display_value,
        icon: SOURCE_TYPE_ICONS[value],
      }
    }),
  ]
}

export const ENDPOINT_STATUS_OPTIONS: Array<
  FilterOption<SpmEndpointStatus | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_statuses" },
  ...(
    ["active", "pending", "error", "disabled"] satisfies SpmEndpointStatus[]
  ).map((status) => ({
    value: status,
    label: endpointStatusStyles[status].label,
    icon: endpointStatusStyles[status].icon,
    iconClassName: endpointStatusStyles[status].iconClassName,
  })),
]

export const FINDING_STATUS_OPTIONS: Array<
  FilterOption<SpmFindingStatus | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_statuses" },
  { value: "open", label: "open" },
  { value: "enforcement_pending", label: "enforcement_pending" },
  { value: "enforced", label: "enforced" },
  { value: "resolved", label: "resolved" },
  { value: "dismissed", label: "dismissed" },
]

export const COMPLIANCE_OPTIONS: Array<
  FilterOption<EndpointComplianceKey | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_compliance" },
  ...(
    [
      "needs_attention",
      "enforcement_queued",
      "compliant",
      "unknown",
    ] satisfies EndpointComplianceKey[]
  ).map((key) => ({
    value: key,
    label: endpointComplianceStyles[key].label,
    icon: endpointComplianceStyles[key].icon,
    iconClassName: endpointComplianceStyles[key].iconClassName,
  })),
]

export const HARNESS_OPTIONS: Array<
  FilterOption<SpmHarness | typeof ALL_VALUE>
> = [
  { value: ALL_VALUE, label: "all_harnesses" },
  { value: "claude_code", label: "claude_code", icon: BotIcon },
]

export function endpointOptions(endpoints: SpmEndpointRead[]) {
  return [
    { value: ALL_VALUE, label: "all_endpoints" },
    ...endpoints.map((endpoint) => ({
      value: endpoint.id,
      label: endpoint.name,
    })),
  ]
}

export function controlOptions(controls: Array<{ id: string; title: string }>) {
  return [
    { value: ALL_VALUE, label: "all_controls" },
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
  const selectedLabel =
    props.options.find((option) => option.value === props.value)?.label ??
    props.options[0]?.label

  return (
    <Select
      value={props.value}
      onValueChange={(value) => props.onChange(value as TValue)}
    >
      <SelectTrigger
        className={cn(
          "h-6 min-w-32 max-w-56 gap-1.5 rounded-md px-2 text-xs font-medium",
          isFiltered && "border-primary/50 bg-primary/5"
        )}
      >
        <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-muted-foreground">
          <Icon className="size-3.5 shrink-0" />
          <span className="whitespace-nowrap">{props.label}</span>
        </div>
        <div className="ml-auto min-w-0 truncate whitespace-nowrap text-foreground">
          {selectedLabel}
        </div>
      </SelectTrigger>
      <SelectContent align="start">
        {props.options.map((option) => {
          const OptionIcon = option.icon ?? Icon
          return (
            <SelectItem key={option.value} value={option.value}>
              <span className="flex items-center gap-2">
                <OptionIcon
                  className={cn(
                    "size-3.5 shrink-0",
                    option.iconClassName ?? "text-muted-foreground"
                  )}
                />
                <span>{option.label}</span>
              </span>
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}

export function MultiFilterSelect<TValue extends string>(props: {
  allowExclude?: boolean
  icon: ComponentType<{ className?: string }>
  label: string
  mode: FilterMode
  onChange: (value: TValue[]) => void
  onModeChange: (mode: FilterMode) => void
  onSortDirectionChange?: (direction: SortDirection) => void
  options: Array<MultiFilterOption<TValue>>
  showSort?: boolean
  sortDirection?: SortDirection
  value: TValue[]
}) {
  return (
    <FilterMultiSelect
      placeholder={props.label}
      icon={props.icon}
      value={props.value}
      onChange={props.onChange}
      options={props.options}
      mode={props.mode}
      onModeChange={props.onModeChange}
      allowExclude={props.allowExclude ?? false}
      showSort={props.showSort}
      sortDirection={props.sortDirection}
      onSortDirectionChange={props.onSortDirectionChange}
    />
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
