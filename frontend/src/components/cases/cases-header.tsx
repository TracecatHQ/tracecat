"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import { format } from "date-fns"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CalendarIcon,
  Check,
  ChevronDown,
  ClockIcon,
  Minus,
  Plus,
  SearchIcon,
  ShieldAlertIcon,
  SignalHighIcon,
  SignalIcon,
  TagIcon,
  UserIcon,
} from "lucide-react"
import {
  type ComponentType,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react"
import type { DateRange } from "react-day-picker"
import type {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseTagRead,
  WorkspaceMember,
} from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Calendar } from "@/components/ui/calendar"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { CaseDateFilterValue, CaseDatePreset } from "@/hooks/use-cases"
import { getDisplayName } from "@/lib/auth"
import { cn } from "@/lib/utils"

const getIconTextClass = (color?: string) =>
  color?.split(" ").find((token) => token.startsWith("text-")) ||
  "text-muted-foreground"

const DATE_PRESET_OPTIONS: Array<{
  value: CaseDatePreset
  label: string
}> = [
  { value: null, label: "Any time" },
  { value: "1d", label: "1 day ago" },
  { value: "3d", label: "3 days ago" },
  { value: "1w", label: "1 week ago" },
  { value: "1m", label: "1 month ago" },
]

function getDateFilterLabel(filter: CaseDateFilterValue): string | null {
  if (filter.type === "preset") {
    if (filter.value === null) return null
    return (
      DATE_PRESET_OPTIONS.find((o) => o.value === filter.value)?.label ?? null
    )
  }
  // Custom range
  if (filter.value.from) {
    if (filter.value.to) {
      return `${format(filter.value.from, "MMM d")} - ${format(filter.value.to, "MMM d")}`
    }
    return `From ${format(filter.value.from, "MMM d")}`
  }
  return null
}

function isDateFilterActive(filter: CaseDateFilterValue): boolean {
  if (filter.type === "preset") {
    return filter.value !== null
  }
  return filter.value.from !== undefined
}

interface DateFilterSelectProps {
  placeholder: string
  icon: typeof ClockIcon | typeof CalendarIcon
  value: CaseDateFilterValue
  onChange: (value: CaseDateFilterValue) => void
  className?: string
}

function DateFilterSelect({
  placeholder,
  icon: Icon,
  value,
  onChange,
  className,
}: DateFilterSelectProps) {
  const [open, setOpen] = useState(false)
  const [showCalendar, setShowCalendar] = useState(false)
  const [dateRange, setDateRange] = useState<DateRange | undefined>(
    value.type === "range" ? value.value : undefined
  )

  useEffect(() => {
    if (value.type === "range") {
      setDateRange(value.value)
    } else {
      setDateRange(undefined)
    }
  }, [value])

  const label = getDateFilterLabel(value)
  const isActive = isDateFilterActive(value)

  const handlePresetSelect = (preset: CaseDatePreset) => {
    onChange({ type: "preset", value: preset })
    setShowCalendar(false)
    if (preset !== null) {
      setOpen(false)
    }
  }

  const handleRangeChange = (range: DateRange | undefined) => {
    setDateRange(range)
    if (range?.from) {
      onChange({ type: "range", value: range })
    }
  }

  const handleOpenChange = (newOpen: boolean) => {
    setOpen(newOpen)
    if (!newOpen) {
      setShowCalendar(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            isActive && "border-primary/50 bg-primary/5",
            className
          )}
        >
          <Icon className="size-3.5 text-muted-foreground" />
          <span>{placeholder}</span>
          {label && (
            <span className="ml-0.5 max-w-[100px] truncate text-[10px] font-medium text-muted-foreground">
              {label}
            </span>
          )}
          <ChevronDown className="size-3 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto p-0 shadow-sm"
        align="start"
        sideOffset={4}
      >
        {showCalendar ? (
          <div className="p-0">
            <div className="flex items-center justify-between border-b px-3 py-2">
              <span className="text-xs font-medium">Select date range</span>
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={() => setShowCalendar(false)}
              >
                Back
              </button>
            </div>
            <Calendar
              mode="range"
              selected={dateRange}
              onSelect={handleRangeChange}
              numberOfMonths={2}
              className="p-3"
            />
            {dateRange?.from && (
              <div className="border-t px-3 py-2">
                <button
                  type="button"
                  className="w-full rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                  onClick={() => setOpen(false)}
                >
                  Apply
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="py-1">
            {DATE_PRESET_OPTIONS.map((option) => (
              <button
                key={option.value ?? "any"}
                type="button"
                className={cn(
                  "flex w-full items-center justify-between px-3 py-1.5 text-xs transition-colors",
                  "hover:bg-muted",
                  value.type === "preset" && value.value === option.value
                    ? "text-foreground"
                    : "text-muted-foreground"
                )}
                onClick={() => handlePresetSelect(option.value)}
              >
                <span>{option.label}</span>
                {value.type === "preset" && value.value === option.value && (
                  <Check className="size-3.5" />
                )}
              </button>
            ))}
            <div className="my-1 border-t" />
            <button
              type="button"
              className={cn(
                "flex w-full items-center gap-2 px-3 py-1.5 text-xs transition-colors",
                "hover:bg-muted",
                value.type === "range"
                  ? "text-foreground"
                  : "text-muted-foreground"
              )}
              onClick={() => setShowCalendar(true)}
            >
              <CalendarIcon className="size-3.5" />
              <span>Custom range...</span>
              {value.type === "range" && <Check className="ml-auto size-3.5" />}
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

interface FilterOption<T extends string = string> {
  value: T
  label: string
  icon?: ComponentType<{ className?: string; style?: React.CSSProperties }>
  iconClassName?: string
  iconStyle?: React.CSSProperties
  /** Custom render function for icons that need dynamic content (e.g., avatars) */
  renderIcon?: () => ReactNode
}

export type FilterMode = "include" | "exclude"
export type SortDirection = "asc" | "desc" | null

interface FilterMultiSelectProps<T extends string> {
  placeholder: string
  icon?: ComponentType<{ className?: string }>
  value: T[]
  options: FilterOption<T>[]
  onChange: (value: T[]) => void
  mode: FilterMode
  onModeChange: (mode: FilterMode) => void
  className?: string
  emptyMessage?: string
  /** Enable sort controls in the dropdown */
  showSort?: boolean
  /** Current sort direction (null means no sorting applied) */
  sortDirection?: SortDirection
  /** Callback when sort direction changes */
  onSortDirectionChange?: (direction: SortDirection) => void
}

function FilterMultiSelect<T extends string>({
  placeholder,
  icon: Icon,
  value,
  options,
  onChange,
  mode,
  onModeChange,
  className,
  emptyMessage = "No results found.",
  showSort = false,
  sortDirection = null,
  onSortDirectionChange,
}: FilterMultiSelectProps<T>) {
  const [open, setOpen] = useState(false)
  const valueSet = useMemo(() => new Set(value), [value])

  const selectedCount = value.length

  const handleSortClick = (direction: "asc" | "desc") => {
    if (!onSortDirectionChange) return
    // Toggle off if already selected, otherwise set the direction
    onSortDirectionChange(sortDirection === direction ? null : direction)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            sortDirection && "border-primary/50 bg-primary/5",
            className
          )}
        >
          {Icon && <Icon className="size-3.5 text-muted-foreground" />}
          <span>{placeholder}</span>
          {selectedCount > 0 && (
            <span className="ml-0.5 text-[10px] font-medium text-muted-foreground">
              {selectedCount}
            </span>
          )}
          {sortDirection && (
            <span className="ml-0.5">
              {sortDirection === "asc" ? (
                <ArrowUpIcon className="size-3 text-muted-foreground" />
              ) : (
                <ArrowDownIcon className="size-3 text-muted-foreground" />
              )}
            </span>
          )}
          <ChevronDown className="size-3 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[220px] p-0 shadow-sm"
        align="start"
        sideOffset={4}
      >
        <div className="flex items-center justify-between border-b px-2 py-1.5">
          <span className="text-[11px] font-medium tracking-wide text-muted-foreground">
            {mode === "exclude" ? "Excluding" : "Including"}
          </span>
          <div className="flex items-center gap-0.5">
            {(["include", "exclude"] as FilterMode[]).map((option) => (
              <button
                key={option}
                type="button"
                className={cn(
                  "flex size-6 items-center justify-center rounded text-xs transition-colors",
                  mode === option
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted/50"
                )}
                aria-label={
                  option === "include" ? "Include filters" : "Exclude filters"
                }
                onClick={() => onModeChange(option)}
              >
                {option === "include" ? (
                  <Plus className="size-3.5" />
                ) : (
                  <Minus className="size-3.5" />
                )}
              </button>
            ))}
          </div>
        </div>
        {showSort && (
          <div className="flex items-center justify-between border-b px-2 py-1.5">
            <span className="text-[11px] font-medium tracking-wide text-muted-foreground">
              Sort
            </span>
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                className={cn(
                  "flex size-6 items-center justify-center rounded text-xs transition-colors",
                  sortDirection === "asc"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted/50"
                )}
                aria-label="Sort ascending"
                onClick={() => handleSortClick("asc")}
              >
                <ArrowUpIcon className="size-3.5" />
              </button>
              <button
                type="button"
                className={cn(
                  "flex size-6 items-center justify-center rounded text-xs transition-colors",
                  sortDirection === "desc"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted/50"
                )}
                aria-label="Sort descending"
                onClick={() => handleSortClick("desc")}
              >
                <ArrowDownIcon className="size-3.5" />
              </button>
            </div>
          </div>
        )}
        <Command>
          <CommandInput
            placeholder={`Search ${placeholder.toLowerCase()}...`}
            className="text-xs"
          />
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = valueSet.has(option.value)
                return (
                  <CommandItem
                    key={option.value}
                    value={`${option.label} ${option.value}`}
                    onSelect={() => {
                      const nextValue = isSelected
                        ? value.filter((item) => item !== option.value)
                        : [...value, option.value]
                      onChange(nextValue)
                      setOpen(true)
                    }}
                    className="flex items-center gap-2 text-xs"
                  >
                    <div
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded-sm border",
                        isSelected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted-foreground/40"
                      )}
                    >
                      {isSelected && <Check className="size-3" aria-hidden />}
                    </div>
                    {option.renderIcon
                      ? option.renderIcon()
                      : option.icon && (
                          <option.icon
                            className={cn("size-3.5", option.iconClassName)}
                            style={option.iconStyle}
                          />
                        )}
                    <span className="truncate">{option.label}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
        {selectedCount > 0 && (
          <div className="border-t p-1">
            <button
              type="button"
              className="flex h-7 w-full items-center justify-center rounded text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={() => onChange([])}
            >
              Clear selection
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

interface CasesHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
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
  members?: WorkspaceMember[]
  tags?: CaseTagRead[]
  // Selection props
  totalCaseCount?: number
  selectedCount?: number
  onSelectAll?: () => void
  onDeselectAll?: () => void
}

export function CasesHeader({
  searchQuery,
  onSearchChange,
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
  members,
  tags,
  totalCaseCount = 0,
  selectedCount = 0,
  onSelectAll,
  onDeselectAll,
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
      const displayName = getDisplayName({
        first_name: member.first_name,
        last_name: member.last_name,
        email: member.email,
      })
      const initials = member.first_name
        ? member.first_name[0].toUpperCase()
        : member.email[0].toUpperCase()

      return {
        value: member.user_id,
        label: displayName,
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
        value: UNASSIGNED,
        label: "Not assigned",
        renderIcon: () => (
          <div className="flex size-5 items-center justify-center">
            <UserIcon className="size-3.5 text-muted-foreground" />
          </div>
        ),
      },
      ...(workspaceMembers || []),
    ]
  }, [members])

  const tagOptions = useMemo<FilterOption<string>[]>(() => {
    return (
      tags?.map((tag) => ({
        value: tag.ref,
        label: tag.name,
        renderIcon: () => (
          <div
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: tag.color || undefined }}
          />
        ),
      })) ?? []
    )
  }, [tags])

  const hasFilters =
    searchQuery.trim().length > 0 ||
    statusFilter.length > 0 ||
    priorityFilter.length > 0 ||
    severityFilter.length > 0 ||
    assigneeFilter.length > 0 ||
    tagFilter.length > 0 ||
    isDateFilterActive(updatedAfter) ||
    isDateFilterActive(createdAfter)

  const handleReset = () => {
    onSearchChange("")
    onStatusChange([])
    onStatusModeChange("include")
    onPriorityChange([])
    onPriorityModeChange("include")
    onSeverityChange([])
    onSeverityModeChange("include")
    onAssigneeChange([])
    onAssigneeModeChange("include")
    onTagChange([])
    onTagModeChange("include")
    onUpdatedAfterChange({ type: "preset", value: null })
    onCreatedAfterChange({ type: "preset", value: null })
  }

  return (
    <div className="shrink-0 border-b">
      {/* Row 1: Search */}
      <header className="flex h-10 items-center border-b pl-3 pr-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center">
            <SearchIcon className="size-4 text-muted-foreground" />
          </div>
          <Input
            type="text"
            placeholder="Search cases..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className={cn(
              "h-7 w-48 border-none bg-transparent p-0",
              "text-sm",
              "shadow-none outline-none",
              "placeholder:text-muted-foreground",
              "focus-visible:ring-0 focus-visible:ring-offset-0"
            )}
          />
        </div>
      </header>

      {/* Row 2: Filter dropdowns */}
      <div className="flex flex-wrap items-center gap-2 py-2 pl-3 pr-4">
        {/* Select all / Deselect all button - matches accordion chevron container (h-7 w-7) */}
        {totalCaseCount > 0 && (
          <button
            type="button"
            onClick={
              selectedCount === totalCaseCount ? onDeselectAll : onSelectAll
            }
            className="flex h-7 w-7 shrink-0 items-center justify-center"
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
        )}

        <FilterMultiSelect
          placeholder="Status"
          icon={SignalIcon}
          value={statusFilter}
          onChange={onStatusChange}
          options={statusOptions}
          mode={statusMode}
          onModeChange={onStatusModeChange}
        />

        <FilterMultiSelect
          placeholder="Priority"
          icon={SignalHighIcon}
          value={priorityFilter}
          onChange={onPriorityChange}
          options={priorityOptions}
          mode={priorityMode}
          onModeChange={onPriorityModeChange}
          showSort
          sortDirection={prioritySortDirection}
          onSortDirectionChange={onPrioritySortDirectionChange}
        />

        <FilterMultiSelect
          placeholder="Severity"
          icon={ShieldAlertIcon}
          value={severityFilter}
          onChange={onSeverityChange}
          options={severityOptions}
          mode={severityMode}
          onModeChange={onSeverityModeChange}
          showSort
          sortDirection={severitySortDirection}
          onSortDirectionChange={onSeveritySortDirectionChange}
        />

        <FilterMultiSelect
          placeholder="Assignee"
          icon={UserIcon}
          value={assigneeFilter}
          onChange={onAssigneeChange}
          options={assigneeOptions}
          mode={assigneeMode}
          onModeChange={onAssigneeModeChange}
          showSort
          sortDirection={assigneeSortDirection}
          onSortDirectionChange={onAssigneeSortDirectionChange}
          emptyMessage={
            members && members.length > 0
              ? "No matching assignees."
              : "No assignees found."
          }
        />

        <FilterMultiSelect
          placeholder="Tags"
          icon={TagIcon}
          value={tagFilter}
          onChange={onTagChange}
          options={tagOptions}
          mode={tagMode}
          onModeChange={onTagModeChange}
          showSort
          sortDirection={tagSortDirection}
          onSortDirectionChange={onTagSortDirectionChange}
          emptyMessage={
            tags && tags.length > 0 ? "No matching tags." : "No tags found."
          }
        />

        <DateFilterSelect
          placeholder="Updated"
          icon={ClockIcon}
          value={updatedAfter}
          onChange={onUpdatedAfterChange}
        />

        <DateFilterSelect
          placeholder="Created"
          icon={CalendarIcon}
          value={createdAfter}
          onChange={onCreatedAfterChange}
        />

        {hasFilters && (
          <button
            type="button"
            onClick={handleReset}
            className="flex h-6 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            Reset
            <Cross2Icon className="size-3" />
          </button>
        )}
      </div>
    </div>
  )
}
