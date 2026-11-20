"use client"

import {
  ArrowDownIcon,
  ArrowUpIcon,
  CalendarIcon,
  Check,
  CircleSlashIcon,
  ClockIcon,
  ListIcon,
  SearchIcon,
  ShieldAlertIcon,
  SignalHighIcon,
  SignalIcon,
  TagIcon,
  TextCursorInputIcon,
  UserIcon,
  WholeWordIcon,
} from "lucide-react"
import { type ComponentType, type ReactNode, useMemo } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseDurationDefinitionRead,
  CaseFieldReadMinimal,
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CasesSearchCasesData,
  CaseTagRead,
  WorkspaceMember,
} from "@/client"

import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { CaseColumnPicker } from "@/components/cases/case-column-picker"
import {
  CaseAddFilterMenu,
  CaseDateFilterChip,
  type CaseFilterProperty,
  CaseMultiFilterChip,
  isDateFilterActive,
} from "@/components/cases/case-filter-chips"
import {
  DropdownDefinitionIcon,
  getDropdownFilterOptions,
  isDropdownFilterActive,
} from "@/components/cases/case-more-filters-menu"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import {
  type CaseSortField,
  type CaseSortValue,
  DEFAULT_CASE_SORT,
} from "@/components/cases/case-sort"
import type {
  FilterMode,
  FilterOption,
  SortDirection,
} from "@/components/filters/filter-multi-select"
import { ListHeaderSearch } from "@/components/list-header-search"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  type CaseDateFilterValue,
  CURRENT_USER,
  DEFAULT_CREATED_PRESET,
  type DropdownFilterState,
} from "@/lib/case-query"
import { formatCaseCount } from "@/lib/cases/case-count"
import { cn } from "@/lib/utils"

const getIconTextClass = (color?: string) =>
  color?.split(" ").find((token) => token.startsWith("text-")) ||
  "text-muted-foreground"

const CASE_SORT_OPTIONS: Array<{
  value: CaseSortField
  label: string
  icon: ComponentType<{ className?: string }>
  description?: string
}> = [
  { value: "updated_at", label: "Updated", icon: ClockIcon },
  { value: "created_at", label: "Created", icon: CalendarIcon },
  { value: "priority", label: "Priority", icon: SignalHighIcon },
  { value: "severity", label: "Severity", icon: ShieldAlertIcon },
  { value: "status", label: "Status", icon: SignalIcon },
  { value: "tasks", label: "Tasks", icon: ListIcon },
  {
    value: "assignee",
    label: "Assignee",
    icon: UserIcon,
    description: "By email; unassigned cases last.",
  },
  {
    value: "tags",
    label: "Tags",
    icon: TagIcon,
    description: "First alphabetical tag; untagged cases last.",
  },
]

function isDefaultCaseSort(value: CaseSortValue): boolean {
  return (
    value.field === DEFAULT_CASE_SORT.field &&
    value.direction === DEFAULT_CASE_SORT.direction
  )
}

const SEARCH_MODE_LABELS = {
  full_text: "Full-text search",
  literal: "Substring search",
} as const

function CaseSearchModeToggle({
  value,
  onChange,
}: {
  value: "full_text" | "literal"
  onChange: (mode: "full_text" | "literal") => void
}) {
  const Icon = value === "literal" ? TextCursorInputIcon : WholeWordIcon
  const next = value === "literal" ? "full_text" : "literal"
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={`${SEARCH_MODE_LABELS[value]}, switch to ${SEARCH_MODE_LABELS[next].toLowerCase()}`}
            onClick={() => onChange(next)}
            className="flex size-6 items-center justify-center rounded-md border border-input bg-transparent text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          >
            <Icon className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent>{SEARCH_MODE_LABELS[value]}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

interface CaseSortSelectProps {
  value: CaseSortValue
  allowRelevance?: boolean
  onChange: (value: CaseSortValue) => void
}

function CaseSortSelect({
  value,
  onChange,
  allowRelevance,
}: CaseSortSelectProps) {
  const options: typeof CASE_SORT_OPTIONS = allowRelevance
    ? [
        { value: "relevance" as const, label: "Relevance", icon: SearchIcon },
        ...CASE_SORT_OPTIONS,
      ]
    : CASE_SORT_OPTIONS
  const selectedLabel =
    options.find((option) => option.value === value.field)?.label ?? "Updated"

  return (
    <div className="inline-flex items-center rounded-md border border-input bg-transparent">
      <Select
        value={value.field}
        onValueChange={(nextField) =>
          onChange({
            field: nextField as CaseSortField,
            direction: nextField === "relevance" ? "desc" : value.direction,
          })
        }
      >
        <SelectTrigger
          aria-label="Case sort field"
          className="h-6 w-[140px] rounded-r-none border-0 bg-transparent px-2 text-xs font-medium shadow-none focus:ring-1 focus:ring-inset focus:ring-ring"
        >
          <span className="text-muted-foreground">Sort by</span>
          <span className="text-foreground">{selectedLabel}</span>
        </SelectTrigger>
        <SelectContent align="start">
          {options.map((option) => {
            const Icon = option.icon
            return (
              <SelectItem
                key={option.value}
                value={option.value}
                textValue={option.label}
              >
                <span className="flex flex-col gap-1">
                  <span className="flex items-center gap-2">
                    <Icon className="size-3.5 text-muted-foreground" />
                    <span>{option.label}</span>
                  </span>
                  {option.description && (
                    <span className="max-w-[220px] text-[11px] text-muted-foreground">
                      {option.description}
                    </span>
                  )}
                </span>
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
      <button
        type="button"
        disabled={value.field === "relevance"}
        className="flex h-6 w-7 items-center justify-center border-l border-input text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        onClick={() =>
          onChange({
            field: value.field,
            direction: value.direction === "asc" ? "desc" : "asc",
          })
        }
        aria-label={
          value.direction === "asc" ? "Sort ascending" : "Sort descending"
        }
      >
        {value.direction === "asc" ? (
          <ArrowUpIcon className="size-3.5" />
        ) : (
          <ArrowDownIcon className="size-3.5" />
        )}
      </button>
    </div>
  )
}

interface CasesHeaderProps {
  searchQuery: string
  searchMode?: "full_text" | "literal"
  onSearchModeChange?: (mode: "full_text" | "literal") => void
  appliedSearchQuery: string
  effectiveHierarchy: NonNullable<CasesSearchCasesData["hierarchy"]>
  isSearchPending: boolean
  onSearchChange: (query: string) => void
  sortBy: CaseSortValue
  onSortByChange: (value: CaseSortValue) => void
  statusFilter: CaseStatus[]
  onStatusChange: (value: CaseStatus[]) => void
  statusMode: FilterMode
  onStatusModeChange: (mode: FilterMode) => void
  priorityFilter: CasePriority[]
  onPriorityChange: (value: CasePriority[]) => void
  priorityMode: FilterMode
  onPriorityModeChange: (mode: FilterMode) => void
  prioritySortDirection?: SortDirection
  onPrioritySortDirectionChange?: (direction: SortDirection) => void
  severityFilter: CaseSeverity[]
  onSeverityChange: (value: CaseSeverity[]) => void
  severityMode: FilterMode
  onSeverityModeChange: (mode: FilterMode) => void
  severitySortDirection?: SortDirection
  onSeveritySortDirectionChange?: (direction: SortDirection) => void
  assigneeFilter: string[]
  onAssigneeChange: (value: string[]) => void
  assigneeMode: FilterMode
  onAssigneeModeChange: (mode: FilterMode) => void
  assigneeSortDirection?: SortDirection
  onAssigneeSortDirectionChange?: (direction: SortDirection) => void
  tagFilter: string[]
  onTagChange: (value: string[]) => void
  tagMode: FilterMode
  onTagModeChange: (mode: FilterMode) => void
  tagSortDirection?: SortDirection
  onTagSortDirectionChange?: (direction: SortDirection) => void
  updatedAfter: CaseDateFilterValue
  onUpdatedAfterChange: (value: CaseDateFilterValue) => void
  createdAfter: CaseDateFilterValue
  onCreatedAfterChange: (value: CaseDateFilterValue) => void
  includeSubCases?: boolean
  onIncludeSubCasesChange?: (value: boolean) => void
  members?: WorkspaceMember[]
  tags?: CaseTagRead[]
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  fieldDefinitions?: CaseFieldReadMinimal[]
  durationDefinitions?: CaseDurationDefinitionRead[]
  visibleColumnIds?: string[]
  onToggleColumn?: (columnId: string) => void
  dropdownFilters: Record<string, DropdownFilterState>
  onDropdownFilterChange: (ref: string, values: string[]) => void
  onDropdownModeChange: (ref: string, mode: FilterMode) => void
  onDropdownSortDirectionChange: (ref: string, direction: SortDirection) => void
  // Selection props
  totalCaseCount?: number
  displayCaseCount?: number | null
  /** displayCaseCount is the server's count cap; more cases match. */
  isDisplayCaseCountCapped?: boolean
  isCountsError?: boolean
  selectedCount?: number
  onSelectAll?: () => void
  onDeselectAll?: () => void
  /** Saved-view controls, when the page supports views. */
  viewBar?: CasesViewBar
}

/** The saved-view controls the cases page places in the header. */
export interface CasesViewBar {
  /** The inline form for naming a view, shown under the search row. */
  panel?: ReactNode
  /** Read-only view conditions, shown with the filter chips. */
  chips?: ReactNode
  /** Clear, reset, and save buttons at the end of the filter row. */
  renderActions: (state: {
    hasFilters: boolean
    clearFilters: () => void
  }) => ReactNode
}

/** Case search, visible scope, and the current view's filter controls. */
export function CasesHeader({
  searchQuery,
  searchMode = "full_text",
  onSearchModeChange,
  appliedSearchQuery,
  effectiveHierarchy,
  isSearchPending,
  onSearchChange,
  sortBy,
  onSortByChange,
  statusFilter,
  onStatusChange,
  statusMode,
  onStatusModeChange,
  priorityFilter,
  onPriorityChange,
  priorityMode,
  onPriorityModeChange,
  prioritySortDirection,
  onPrioritySortDirectionChange,
  severityFilter,
  onSeverityChange,
  severityMode,
  onSeverityModeChange,
  severitySortDirection,
  onSeveritySortDirectionChange,
  assigneeFilter,
  onAssigneeChange,
  assigneeMode,
  onAssigneeModeChange,
  assigneeSortDirection,
  onAssigneeSortDirectionChange,
  tagFilter,
  onTagChange,
  tagMode,
  onTagModeChange,
  tagSortDirection,
  onTagSortDirectionChange,
  updatedAfter,
  onUpdatedAfterChange,
  createdAfter,
  onCreatedAfterChange,
  includeSubCases = false,
  onIncludeSubCasesChange,
  members,
  tags,
  dropdownDefinitions,
  fieldDefinitions,
  durationDefinitions,
  visibleColumnIds,
  onToggleColumn,
  dropdownFilters,
  onDropdownFilterChange,
  onDropdownModeChange,
  onDropdownSortDirectionChange,
  totalCaseCount = 0,
  displayCaseCount = null,
  isDisplayCaseCountCapped = false,
  isCountsError = false,
  selectedCount = 0,
  onSelectAll,
  onDeselectAll,
  viewBar,
}: CasesHeaderProps) {
  const statusOptions = useMemo<FilterOption<CaseStatus>[]>(() => {
    return Object.values(STATUSES).map((status) => ({
      value: status.value,
      label: status.label,
      icon: status.icon,
      iconClassName: getIconTextClass(status.color),
    }))
  }, [])

  const priorityOptions = useMemo<FilterOption<CasePriority>[]>(() => {
    return Object.values(PRIORITIES).map((priority) => ({
      value: priority.value,
      label: priority.label,
      icon: priority.icon,
      iconClassName: getIconTextClass(priority.color),
    }))
  }, [])

  const severityOptions = useMemo<FilterOption<CaseSeverity>[]>(() => {
    return Object.values(SEVERITIES).map((severity) => ({
      value: severity.value,
      label: severity.label,
      icon: severity.icon,
      iconClassName: getIconTextClass(severity.color),
    }))
  }, [])

  const assigneeOptions = useMemo<FilterOption<string>[]>(() => {
    const workspaceMembers = members?.map((member) => {
      const initials = member.email[0].toUpperCase()

      return {
        value: member.user_id,
        label: member.email,
        renderIcon: () => (
          <Avatar className="size-5">
            <AvatarFallback className="text-[10px] font-medium">
              {initials}
            </AvatarFallback>
          </Avatar>
        ),
      }
    })

    return [
      {
        value: CURRENT_USER,
        label: "Me",
        renderIcon: () => (
          <div className="flex size-5 items-center justify-center">
            <UserIcon className="size-3 text-muted-foreground" />
          </div>
        ),
      },
      {
        value: UNASSIGNED,
        label: "Unassigned",
        renderIcon: () => (
          <div className="flex size-5 items-center justify-center">
            <CircleSlashIcon className="size-3 text-muted-foreground/50" />
          </div>
        ),
      },
      ...(workspaceMembers ?? []).sort((a, b) =>
        a.label.localeCompare(b.label)
      ),
    ]
  }, [members])

  const tagOptions = useMemo<FilterOption<string>[]>(() => {
    return (
      tags
        ?.map((tag) => ({
          value: tag.ref,
          label: tag.name,
          renderIcon: () => (
            <div
              className="size-2 shrink-0 rounded-full"
              style={{ backgroundColor: tag.color || undefined }}
            />
          ),
        }))
        .sort((a, b) => a.label.localeCompare(b.label)) ?? []
    )
  }, [tags])

  const hasDropdownFilters = Object.values(dropdownFilters).some(
    (s) => s.values.length > 0
  )
  const hasDefaultCreatedAfter =
    createdAfter.type === "preset" &&
    createdAfter.value === DEFAULT_CREATED_PRESET

  const countNoun = displayCaseCount === 1 ? "case" : "cases"
  const includesSubCases = effectiveHierarchy === "all"

  const hasFilters =
    searchQuery.trim().length > 0 ||
    !isDefaultCaseSort(sortBy) ||
    statusFilter.length > 0 ||
    priorityFilter.length > 0 ||
    severityFilter.length > 0 ||
    assigneeFilter.length > 0 ||
    tagFilter.length > 0 ||
    hasDropdownFilters ||
    isDateFilterActive(updatedAfter) ||
    (isDateFilterActive(createdAfter) && !hasDefaultCreatedAfter) ||
    (includeSubCases && onIncludeSubCasesChange !== undefined)

  const handleReset = () => {
    onSearchChange("")
    onSearchModeChange?.("full_text")
    onStatusChange([])
    onStatusModeChange("include")
    onPriorityChange([])
    onPriorityModeChange("include")
    onPrioritySortDirectionChange?.(null)
    onSeverityChange([])
    onSeverityModeChange("include")
    onSeveritySortDirectionChange?.(null)
    onAssigneeChange([])
    onAssigneeModeChange("include")
    onAssigneeSortDirectionChange?.(null)
    onTagChange([])
    onTagModeChange("include")
    onTagSortDirectionChange?.(null)
    // Reset dropdown filters
    const refs = new Set([
      ...(dropdownDefinitions?.map((d) => d.ref) ?? []),
      ...Object.keys(dropdownFilters),
    ])
    for (const ref of refs) {
      onDropdownFilterChange(ref, [])
      onDropdownModeChange(ref, "include")
      onDropdownSortDirectionChange(ref, null)
    }
    onUpdatedAfterChange({ type: "preset", value: null })
    onCreatedAfterChange({
      type: "preset",
      value: DEFAULT_CREATED_PRESET,
    })
    onIncludeSubCasesChange?.(false)
    onSortByChange(
      onSearchModeChange
        ? { field: "relevance", direction: "desc" }
        : DEFAULT_CASE_SORT
    )
  }

  const filterProperties: CaseFilterProperty[] = [
    {
      kind: "multi",
      key: "status",
      label: "Status",
      icon: <SignalIcon className="size-3.5 text-muted-foreground" />,
      options: statusOptions,
      value: statusFilter,
      onChange: (value) => onStatusChange(value as CaseStatus[]),
    },
    {
      kind: "multi",
      key: "priority",
      label: "Priority",
      icon: <SignalHighIcon className="size-3.5 text-muted-foreground" />,
      options: priorityOptions,
      value: priorityFilter,
      onChange: (value) => onPriorityChange(value as CasePriority[]),
    },
    {
      kind: "multi",
      key: "severity",
      label: "Severity",
      icon: <ShieldAlertIcon className="size-3.5 text-muted-foreground" />,
      options: severityOptions,
      value: severityFilter,
      onChange: (value) => onSeverityChange(value as CaseSeverity[]),
    },
    {
      kind: "multi",
      key: "assignee",
      label: "Assignee",
      icon: <UserIcon className="size-3.5 text-muted-foreground" />,
      options: assigneeOptions,
      value: assigneeFilter,
      onChange: onAssigneeChange,
    },
    {
      kind: "multi",
      key: "tags",
      label: "Tags",
      icon: <TagIcon className="size-3.5 text-muted-foreground" />,
      options: tagOptions,
      value: tagFilter,
      onChange: onTagChange,
    },
    ...(dropdownDefinitions ?? []).map(
      (definition): CaseFilterProperty => ({
        kind: "multi",
        key: `dropdown:${definition.ref}`,
        label: definition.name,
        icon: <DropdownDefinitionIcon definition={definition} />,
        options: getDropdownFilterOptions(definition),
        value: dropdownFilters[definition.ref]?.values ?? [],
        onChange: (values) => onDropdownFilterChange(definition.ref, values),
      })
    ),
    {
      kind: "date",
      key: "updated",
      label: "Updated",
      icon: <ClockIcon className="size-3.5 text-muted-foreground" />,
      value: updatedAfter,
      onChange: onUpdatedAfterChange,
    },
    {
      kind: "date",
      key: "created",
      label: "Created",
      icon: <CalendarIcon className="size-3.5 text-muted-foreground" />,
      value: createdAfter,
      onChange: onCreatedAfterChange,
    },
  ]

  const hasFilterChips =
    statusFilter.length > 0 ||
    priorityFilter.length > 0 ||
    severityFilter.length > 0 ||
    assigneeFilter.length > 0 ||
    tagFilter.length > 0 ||
    hasDropdownFilters ||
    isDateFilterActive(updatedAfter) ||
    isDateFilterActive(createdAfter)

  return (
    <div className="shrink-0 border-b">
      {/* Row 1: Search */}
      <header className="flex h-10 items-center gap-3 border-b pl-3 pr-4">
        <ListHeaderSearch
          aria-label="Search cases"
          aria-describedby="case-search-scope"
          placeholder={
            searchMode === "literal"
              ? "Search exact characters..."
              : "Search cases by title, description, or ID..."
          }
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
        />

        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span className="text-xs tabular-nums text-muted-foreground">
            {formatCaseCount(
              displayCaseCount ?? totalCaseCount,
              isDisplayCaseCountCapped && displayCaseCount !== null
            )}{" "}
            {displayCaseCount === null ? "loaded" : countNoun}
          </span>
          {onSearchModeChange && (
            <CaseSearchModeToggle
              value={searchMode}
              onChange={onSearchModeChange}
            />
          )}
          {((onToggleColumn && visibleColumnIds) ||
            onIncludeSubCasesChange) && (
            <CaseColumnPicker
              dropdownDefinitions={dropdownDefinitions}
              fieldDefinitions={fieldDefinitions}
              durationDefinitions={durationDefinitions}
              visibleColumnIds={visibleColumnIds}
              onToggle={onToggleColumn}
              includesSubCases={includesSubCases}
              hierarchyDisabled={Boolean(appliedSearchQuery)}
              onIncludeSubCasesChange={onIncludeSubCasesChange}
            />
          )}
        </div>
      </header>

      {viewBar?.panel}

      <p id="case-search-scope" role="status" className="sr-only">
        {[
          isSearchPending && "Updating search…",
          isCountsError && "Case count unavailable",
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>

      {/* Row 2: Applied filters, sort, and view actions */}
      <div className="flex items-start gap-2 py-2 pl-3 pr-4">
        {/* Select all keeps the accordion chevron column (w-7) so it stays
            aligned with case rows while the filters wrap beside it. */}
        {totalCaseCount > 0 ? (
          <button
            type="button"
            onClick={
              selectedCount === totalCaseCount ? onDeselectAll : onSelectAll
            }
            className="flex h-6 w-7 shrink-0 items-center justify-center"
            aria-label={
              selectedCount === totalCaseCount ? "Deselect all" : "Select all"
            }
            title={
              selectedCount === totalCaseCount ? "Deselect all" : "Select all"
            }
          >
            <div
              className={cn(
                "flex size-4 shrink-0 items-center justify-center rounded-sm border transition-colors",
                selectedCount === totalCaseCount
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-muted-foreground/40 bg-transparent"
              )}
            >
              {selectedCount === totalCaseCount && (
                <Check className="size-3" aria-hidden />
              )}
            </div>
          </button>
        ) : (
          <div className="h-6 w-7 shrink-0" aria-hidden />
        )}

        <fieldset className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          <legend className="sr-only">Current view filters</legend>
          {statusFilter.length > 0 && (
            <CaseMultiFilterChip
              key="status"
              label="Status"
              noun="statuses"
              icon={<SignalIcon className="size-3.5 text-muted-foreground" />}
              options={statusOptions}
              value={statusFilter}
              onChange={(value) => onStatusChange(value as CaseStatus[])}
              mode={statusMode}
              onModeChange={onStatusModeChange}
            />
          )}
          {priorityFilter.length > 0 && (
            <CaseMultiFilterChip
              key="priority"
              label="Priority"
              noun="priorities"
              icon={
                <SignalHighIcon className="size-3.5 text-muted-foreground" />
              }
              options={priorityOptions}
              value={priorityFilter}
              onChange={(value) => onPriorityChange(value as CasePriority[])}
              mode={priorityMode}
              onModeChange={onPriorityModeChange}
            />
          )}
          {severityFilter.length > 0 && (
            <CaseMultiFilterChip
              key="severity"
              label="Severity"
              noun="severities"
              icon={
                <ShieldAlertIcon className="size-3.5 text-muted-foreground" />
              }
              options={severityOptions}
              value={severityFilter}
              onChange={(value) => onSeverityChange(value as CaseSeverity[])}
              mode={severityMode}
              onModeChange={onSeverityModeChange}
            />
          )}
          {assigneeFilter.length > 0 && (
            <CaseMultiFilterChip
              key="assignee"
              label="Assignee"
              noun="assignees"
              icon={<UserIcon className="size-3.5 text-muted-foreground" />}
              options={assigneeOptions}
              value={assigneeFilter}
              onChange={onAssigneeChange}
              mode={assigneeMode}
              onModeChange={onAssigneeModeChange}
              allowExclude={false}
              emptyMessage="No matching assignees."
            />
          )}
          {tagFilter.length > 0 && (
            <CaseMultiFilterChip
              key="tags"
              label="Tags"
              noun="tags"
              icon={<TagIcon className="size-3.5 text-muted-foreground" />}
              options={tagOptions}
              value={tagFilter}
              onChange={onTagChange}
              mode={tagMode}
              onModeChange={onTagModeChange}
              allowExclude={false}
              emptyMessage="No matching tags."
            />
          )}

          {dropdownDefinitions
            ?.filter((def) => isDropdownFilterActive(dropdownFilters[def.ref]))
            .map((def) => {
              const state = dropdownFilters[def.ref]
              return (
                <CaseMultiFilterChip
                  key={`dropdown:${def.id}`}
                  label={def.name}
                  noun="options"
                  icon={<DropdownDefinitionIcon definition={def} />}
                  options={getDropdownFilterOptions(def)}
                  value={state.values}
                  onChange={(values) => onDropdownFilterChange(def.ref, values)}
                  mode={state.mode}
                  onModeChange={(mode) => onDropdownModeChange(def.ref, mode)}
                  allowExclude={false}
                />
              )
            })}

          {dropdownDefinitions &&
            Object.entries(dropdownFilters)
              .filter(
                ([ref, state]) =>
                  state.values.length > 0 &&
                  !dropdownDefinitions.some(
                    (definition) => definition.ref === ref
                  )
              )
              .map(([ref, state]) => (
                <Button
                  key={ref}
                  variant="outline"
                  size="sm"
                  className="h-6 text-xs"
                  onClick={() => onDropdownFilterChange(ref, [])}
                >
                  Unavailable dropdown {ref}: {state.values.join(", ")} ×
                </Button>
              ))}

          {isDateFilterActive(updatedAfter) && (
            <CaseDateFilterChip
              key="updated"
              label="Updated"
              icon={<ClockIcon className="size-3.5 text-muted-foreground" />}
              value={updatedAfter}
              onChange={onUpdatedAfterChange}
              onRemove={() =>
                onUpdatedAfterChange({ type: "preset", value: null })
              }
            />
          )}
          {isDateFilterActive(createdAfter) && (
            <CaseDateFilterChip
              key="created"
              label="Created"
              icon={<CalendarIcon className="size-3.5 text-muted-foreground" />}
              value={createdAfter}
              onChange={onCreatedAfterChange}
              onRemove={() =>
                onCreatedAfterChange({ type: "preset", value: null })
              }
            />
          )}

          {viewBar?.chips}

          <CaseAddFilterMenu
            properties={filterProperties}
            showLabel={!hasFilterChips}
          />
        </fieldset>

        <div className="flex shrink-0 items-center gap-1.5">
          <CaseSortSelect
            value={sortBy}
            onChange={onSortByChange}
            allowRelevance={
              searchMode === "full_text" && Boolean(appliedSearchQuery)
            }
          />
          {viewBar
            ? viewBar.renderActions({ hasFilters, clearFilters: handleReset })
            : hasFilters && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-xs"
                  onClick={handleReset}
                >
                  Clear
                </Button>
              )}
        </div>
      </div>
    </div>
  )
}
