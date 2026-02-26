"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import { format } from "date-fns"
import {
  CalendarIcon,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClockIcon,
  FolderIcon,
  ListIcon,
  SearchIcon,
  TagIcon,
} from "lucide-react"
import { type ReactNode, useEffect, useMemo, useState } from "react"
import type { DateRange } from "react-day-picker"
import type { TagRead } from "@/client"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export type WorkflowsViewMode = "list" | "folders"
export type WorkflowsDatePreset = "1d" | "3d" | "1w" | "1m" | null
export type WorkflowsDateFilterValue =
  | { type: "preset"; value: WorkflowsDatePreset }
  | { type: "range"; value: DateRange }

export const DEFAULT_WORKFLOW_DATE_FILTER: WorkflowsDateFilterValue = {
  type: "preset",
  value: null,
}

const DATE_PRESET_OPTIONS: Array<{
  value: WorkflowsDatePreset
  label: string
}> = [
  { value: null, label: "Any time" },
  { value: "1d", label: "1 day ago" },
  { value: "3d", label: "3 days ago" },
  { value: "1w", label: "1 week ago" },
  { value: "1m", label: "1 month ago" },
]

const LIMIT_OPTIONS = [10, 20, 50]

function getDateFilterLabel(filter: WorkflowsDateFilterValue): string | null {
  if (filter.type === "preset") {
    if (filter.value === null) return null
    return (
      DATE_PRESET_OPTIONS.find((option) => option.value === filter.value)
        ?.label ?? null
    )
  }

  if (filter.value.from) {
    if (filter.value.to) {
      return `${format(filter.value.from, "MMM d")} - ${format(filter.value.to, "MMM d")}`
    }
    return `From ${format(filter.value.from, "MMM d")}`
  }

  return null
}

export function isDateFilterActive(filter: WorkflowsDateFilterValue): boolean {
  if (filter.type === "preset") {
    return filter.value !== null
  }
  return filter.value.from !== undefined
}

interface DateFilterSelectProps {
  placeholder: string
  icon: typeof ClockIcon | typeof CalendarIcon
  value: WorkflowsDateFilterValue
  onChange: (value: WorkflowsDateFilterValue) => void
}

function DateFilterSelect({
  placeholder,
  icon: Icon,
  value,
  onChange,
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

  const handlePresetSelect = (preset: WorkflowsDatePreset) => {
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

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen)
    if (!nextOpen) {
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
            isActive && "border-primary/50 bg-primary/5"
          )}
        >
          <Icon className="size-3.5 text-muted-foreground" />
          <span>{placeholder}</span>
          {label && (
            <span className="ml-0.5 max-w-[110px] truncate text-[10px] font-medium text-muted-foreground">
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

interface TagFilterSelectProps {
  value: string[]
  options: TagRead[]
  onChange: (next: string[]) => void
}

function TagFilterSelect({ value, options, onChange }: TagFilterSelectProps) {
  const [open, setOpen] = useState(false)
  const valueSet = useMemo(() => new Set(value), [value])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors",
            "hover:bg-muted/50",
            value.length > 0 && "border-primary/50 bg-primary/5"
          )}
        >
          <TagIcon className="size-3.5 text-muted-foreground" />
          <span>Tags</span>
          {value.length > 0 && (
            <span className="ml-0.5 text-[10px] font-medium text-muted-foreground">
              {value.length}
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
        <Command>
          <CommandInput placeholder="Search tags..." className="text-xs" />
          <CommandList>
            <CommandEmpty>
              {options.length > 0 ? "No matching tags." : "No tags found."}
            </CommandEmpty>
            <CommandGroup>
              {options.map((tag) => {
                const isSelected = valueSet.has(tag.ref)
                return (
                  <CommandItem
                    key={tag.id}
                    value={`${tag.name} ${tag.ref}`}
                    onSelect={() => {
                      const nextValue = isSelected
                        ? value.filter((item) => item !== tag.ref)
                        : [...value, tag.ref]
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
                    <div
                      className={cn(
                        "size-2 shrink-0 rounded-full",
                        !tag.color && "bg-muted"
                      )}
                      style={{ backgroundColor: tag.color || undefined }}
                    />
                    <span className="truncate">{tag.name}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
        {value.length > 0 && (
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

interface WorkflowsHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  view: WorkflowsViewMode
  onViewChange: (view: WorkflowsViewMode) => void
  tags?: TagRead[]
  tagFilter: string[]
  onTagChange: (value: string[]) => void
  updatedAfter: WorkflowsDateFilterValue
  onUpdatedAfterChange: (value: WorkflowsDateFilterValue) => void
  createdAfter: WorkflowsDateFilterValue
  onCreatedAfterChange: (value: WorkflowsDateFilterValue) => void
  totalCount: number
  countLabel: string
  limit: number
  onLimitChange: (limit: number) => void
  hasPreviousPage: boolean
  hasNextPage: boolean
  onPreviousPage: () => void
  onNextPage: () => void
  isPaginationLoading?: boolean
}

export function WorkflowsHeader({
  searchQuery,
  onSearchChange,
  view,
  onViewChange,
  tags,
  tagFilter,
  onTagChange,
  updatedAfter,
  onUpdatedAfterChange,
  createdAfter,
  onCreatedAfterChange,
  totalCount,
  countLabel,
  limit,
  onLimitChange,
  hasPreviousPage,
  hasNextPage,
  onPreviousPage,
  onNextPage,
  isPaginationLoading = false,
}: WorkflowsHeaderProps) {
  const hasFilters =
    searchQuery.trim().length > 0 ||
    tagFilter.length > 0 ||
    isDateFilterActive(updatedAfter) ||
    isDateFilterActive(createdAfter)

  const handleReset = () => {
    onSearchChange("")
    onTagChange([])
    onUpdatedAfterChange(DEFAULT_WORKFLOW_DATE_FILTER)
    onCreatedAfterChange(DEFAULT_WORKFLOW_DATE_FILTER)
  }

  const viewLabel: Record<WorkflowsViewMode, string> = {
    list: "List",
    folders: "Folders",
  }

  const viewIcon: Record<WorkflowsViewMode, ReactNode> = {
    list: <ListIcon className="size-3.5 text-muted-foreground" />,
    folders: <FolderIcon className="size-3.5 text-muted-foreground" />,
  }

  return (
    <div className="shrink-0 border-b">
      <header className="flex h-10 items-center border-b pl-3 pr-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center">
            <SearchIcon className="size-4 text-muted-foreground" />
          </div>
          <Input
            type="text"
            placeholder="Search workflows..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className={cn(
              "h-7 w-56 border-none bg-transparent p-0",
              "text-sm",
              "shadow-none outline-none",
              "placeholder:text-muted-foreground",
              "focus-visible:ring-0 focus-visible:ring-offset-0"
            )}
          />
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {totalCount} {countLabel}
          </span>
          <button
            type="button"
            className={cn(
              "inline-flex h-7 items-center gap-1 px-1 text-xs text-muted-foreground transition-colors",
              hasPreviousPage
                ? "hover:text-foreground"
                : "cursor-not-allowed opacity-50"
            )}
            onClick={onPreviousPage}
            disabled={!hasPreviousPage || isPaginationLoading}
            aria-label="Previous page"
          >
            <ChevronLeft className="size-3.5" />
            <span>Prev</span>
          </button>
          <button
            type="button"
            className={cn(
              "inline-flex h-7 items-center gap-1 px-1 text-xs text-muted-foreground transition-colors",
              hasNextPage
                ? "hover:text-foreground"
                : "cursor-not-allowed opacity-50"
            )}
            onClick={onNextPage}
            disabled={!hasNextPage || isPaginationLoading}
            aria-label="Next page"
          >
            <span>Next</span>
            <ChevronRight className="size-3.5" />
          </button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <Select
          value={view}
          onValueChange={(nextView) =>
            onViewChange(nextView as WorkflowsViewMode)
          }
        >
          <SelectTrigger className="h-6 w-[138px] rounded-md px-2 text-xs font-medium">
            <div className="flex items-center gap-1.5">
              {viewIcon[view]}
              <span>View</span>
            </div>
            <SelectValue placeholder={viewLabel[view]} />
          </SelectTrigger>
          <SelectContent align="start">
            <SelectItem value="list">List</SelectItem>
            <SelectItem value="folders">Folders</SelectItem>
          </SelectContent>
        </Select>

        <TagFilterSelect
          value={tagFilter}
          options={tags ?? []}
          onChange={onTagChange}
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

        <div className="ml-auto flex items-center">
          <Select
            value={`${limit}`}
            onValueChange={(value) => onLimitChange(Number(value))}
            disabled={isPaginationLoading}
          >
            <SelectTrigger className="h-6 w-auto gap-1.5 rounded-md px-2 text-xs font-medium">
              <span className="text-muted-foreground">Limit</span>
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {LIMIT_OPTIONS.map((option) => (
                <SelectItem key={option} value={`${option}`}>
                  {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  )
}
