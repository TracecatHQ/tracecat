"use client"

import { format } from "date-fns"
import {
  ArrowLeftIcon,
  CalendarIcon,
  Check,
  ListFilterIcon,
  PlusIcon,
  XIcon,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import type { DateRange } from "react-day-picker"
import type {
  FilterMode,
  FilterOption,
} from "@/components/filters/filter-multi-select"
import { Calendar } from "@/components/ui/calendar"
import { CheckIndicator } from "@/components/ui/check-indicator"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { CaseDateFilterValue, CaseDatePreset } from "@/lib/case-query"
import { cn } from "@/lib/utils"

/** Option lists this long or longer get a search box. */
const SEARCH_MIN_OPTIONS = 8

const SEGMENT_CLASS =
  "flex h-full min-w-0 items-center gap-1.5 border-l border-input px-2 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"

export const DATE_PRESET_OPTIONS: Array<{
  value: CaseDatePreset
  label: string
}> = [
  { value: null, label: "Any time" },
  { value: "1d", label: "1 day ago" },
  { value: "3d", label: "3 days ago" },
  { value: "1w", label: "1 week ago" },
  { value: "1m", label: "1 month ago" },
]

/** A short label for a date filter, or null when it matches any time. */
export function getDateFilterLabel(filter: CaseDateFilterValue): string | null {
  if (filter.type === "preset") {
    if (filter.value === null) return null
    return (
      DATE_PRESET_OPTIONS.find((o) => o.value === filter.value)?.label ?? null
    )
  }
  if (filter.value.from) {
    if (filter.value.to) {
      return `${format(filter.value.from, "MMM d")} – ${format(filter.value.to, "MMM d")}`
    }
    return format(filter.value.from, "MMM d")
  }
  return null
}

/** Whether a date filter narrows the list. */
export function isDateFilterActive(filter: CaseDateFilterValue): boolean {
  if (filter.type === "preset") {
    return filter.value !== null
  }
  return filter.value.from !== undefined
}

/** One property the add-filter menu offers. */
export type CaseFilterProperty =
  | {
      kind: "multi"
      key: string
      label: string
      icon: ReactNode
      options: FilterOption<string>[]
      value: string[]
      onChange: (value: string[]) => void
    }
  | {
      kind: "date"
      key: string
      label: string
      icon: ReactNode
      value: CaseDateFilterValue
      onChange: (value: CaseDateFilterValue) => void
    }

function OptionIcon({ option }: { option: FilterOption<string> }) {
  if (option.renderIcon) {
    return <>{option.renderIcon()}</>
  }
  if (option.icon) {
    return (
      <option.icon
        className={cn("size-3.5 shrink-0", option.iconClassName)}
        style={option.iconStyle}
      />
    )
  }
  return null
}

/** A searchable, multi-select list of filter options. */
function FilterOptionList({
  label,
  options,
  value,
  onChange,
  emptyMessage = "No results.",
}: {
  label: string
  options: FilterOption<string>[]
  value: string[]
  onChange: (value: string[]) => void
  emptyMessage?: string
}) {
  const selected = new Set(value)
  return (
    <Command>
      {options.length >= SEARCH_MIN_OPTIONS && (
        <CommandInput
          autoFocus
          placeholder={`${label}...`}
          className="text-xs"
        />
      )}
      <CommandList>
        <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
          {emptyMessage}
        </CommandEmpty>
        <CommandGroup>
          {options.map((option) => {
            const isSelected = selected.has(option.value)
            return (
              <CommandItem
                key={option.value}
                value={`${option.label} ${option.value}`}
                onSelect={() =>
                  onChange(
                    isSelected
                      ? value.filter((item) => item !== option.value)
                      : [...value, option.value]
                  )
                }
                className="group flex items-center gap-2 text-xs"
              >
                <CheckIndicator checked={isSelected} />
                <OptionIcon option={option} />
                <span
                  className={cn("truncate", option.labelClassName)}
                  style={option.labelStyle}
                >
                  {option.label}
                </span>
              </CommandItem>
            )
          })}
        </CommandGroup>
      </CommandList>
    </Command>
  )
}

/** Preset choices and a custom range for a date filter. */
function DateFilterOptions({
  value,
  onChange,
  onDone,
}: {
  value: CaseDateFilterValue
  onChange: (value: CaseDateFilterValue) => void
  onDone: () => void
}) {
  const [showCalendar, setShowCalendar] = useState(false)
  const [range, setRange] = useState<DateRange | undefined>(
    value.type === "range" ? value.value : undefined
  )

  if (showCalendar) {
    return (
      <div>
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
          selected={range}
          onSelect={(next) => {
            setRange(next)
            if (next?.from) {
              onChange({ type: "range", value: next })
            }
          }}
          numberOfMonths={2}
          className="p-3"
        />
        {range?.from && (
          <div className="border-t px-3 py-2">
            <button
              type="button"
              className="w-full rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
              onClick={onDone}
            >
              Apply
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="py-1">
      {DATE_PRESET_OPTIONS.filter((option) => option.value !== null).map(
        (option) => {
          const isSelected =
            value.type === "preset" && value.value === option.value
          return (
            <button
              key={option.value}
              type="button"
              className={cn(
                "flex w-full items-center justify-between px-3 py-1.5 text-xs transition-colors hover:bg-muted",
                isSelected ? "text-foreground" : "text-muted-foreground"
              )}
              onClick={() => {
                onChange({ type: "preset", value: option.value })
                onDone()
              }}
            >
              <span>{option.label}</span>
              {isSelected && <Check className="size-3.5" />}
            </button>
          )
        }
      )}
      <div className="my-1 border-t" />
      <button
        type="button"
        className={cn(
          "flex w-full items-center gap-2 px-3 py-1.5 text-xs transition-colors hover:bg-muted",
          value.type === "range" ? "text-foreground" : "text-muted-foreground"
        )}
        onClick={() => setShowCalendar(true)}
      >
        <CalendarIcon className="size-3.5" />
        <span>Custom range...</span>
        {value.type === "range" && <Check className="ml-auto size-3.5" />}
      </button>
    </div>
  )
}

/**
 * The frame of an applied filter: property, operator, value, and remove.
 *
 * Each part is its own segment, as in "Status | is any of | 2 statuses | x".
 */
function FilterChipFrame({
  icon,
  label,
  operator,
  children,
  onRemove,
}: {
  icon: ReactNode
  label: string
  operator: ReactNode
  children: ReactNode
  onRemove: () => void
}) {
  return (
    <div
      role="group"
      aria-label={`${label} filter`}
      className="flex h-6 max-w-full items-center overflow-hidden rounded-md border border-input text-xs"
    >
      <span className="flex h-full shrink-0 items-center gap-1.5 px-2 font-medium">
        {icon}
        {label}
      </span>
      {operator}
      {children}
      <button
        type="button"
        aria-label={`Remove ${label.toLowerCase()} filter`}
        className="flex h-full w-6 shrink-0 items-center justify-center border-l border-input text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        onClick={onRemove}
      >
        <XIcon className="size-3" />
      </button>
    </div>
  )
}

function operatorLabel(mode: FilterMode, count: number): string {
  if (mode === "exclude") {
    return count > 1 ? "is none of" : "is not"
  }
  return count > 1 ? "is any of" : "is"
}

interface CaseMultiFilterChipProps {
  label: string
  icon: ReactNode
  /** Plural noun for the value summary, for example "statuses". */
  noun: string
  options: FilterOption<string>[]
  value: string[]
  onChange: (value: string[]) => void
  mode: FilterMode
  onModeChange: (mode: FilterMode) => void
  allowExclude?: boolean
  emptyMessage?: string
}

/** An applied multi-value filter, such as status or tags. */
export function CaseMultiFilterChip({
  label,
  icon,
  noun,
  options,
  value,
  onChange,
  mode,
  onModeChange,
  allowExclude = true,
  emptyMessage,
}: CaseMultiFilterChipProps) {
  const [open, setOpen] = useState(false)
  const selected = useMemo(
    () => options.filter((option) => value.includes(option.value)),
    [options, value]
  )
  const count = value.length
  const operatorText = operatorLabel(mode, count)

  const operator = allowExclude ? (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`${label} operator: ${operatorText}`}
          className={cn(SEGMENT_CLASS, "shrink-0 text-muted-foreground")}
        >
          {operatorText}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-32 text-xs">
        {(["include", "exclude"] as const).map((option) => (
          <DropdownMenuItem
            key={option}
            className="text-xs"
            onSelect={() => onModeChange(option)}
          >
            {operatorLabel(option, count)}
            {mode === option && <Check className="ml-auto size-3.5" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  ) : (
    <span className="flex h-full shrink-0 items-center border-l border-input px-2 text-muted-foreground">
      {operatorText}
    </span>
  )

  // Values can outlive their options, for example a deleted tag. Name them
  // as unavailable so the summary always matches the operator's count.
  const missing = count - selected.length
  let summary: ReactNode
  if (selected.length === 0) {
    summary = (
      <span className="truncate text-muted-foreground">
        {count === 1 ? "1 unavailable value" : `${count} unavailable values`}
      </span>
    )
  } else if (count === 1) {
    summary = (
      <>
        <OptionIcon option={selected[0]} />
        <span className="truncate" style={selected[0].labelStyle}>
          {selected[0].label}
        </span>
      </>
    )
  } else {
    summary = (
      <>
        <span className="flex shrink-0 items-center -space-x-1">
          {selected.slice(0, 3).map((option) => (
            <span
              key={option.value}
              className="flex items-center rounded-full bg-background"
            >
              <OptionIcon option={option} />
            </span>
          ))}
        </span>
        <span className="truncate">
          {count} {noun}
          {missing > 0 && (
            <span className="text-muted-foreground">
              {" "}
              ({missing} unavailable)
            </span>
          )}
        </span>
      </>
    )
  }

  return (
    <FilterChipFrame
      icon={icon}
      label={label}
      operator={operator}
      onRemove={() => onChange([])}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`${label} values`}
            className={cn(SEGMENT_CLASS, "max-w-[220px]")}
          >
            {summary}
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-56 p-0 shadow-sm" align="start">
          <FilterOptionList
            label={label}
            options={options}
            value={value}
            onChange={onChange}
            emptyMessage={emptyMessage}
          />
        </PopoverContent>
      </Popover>
    </FilterChipFrame>
  )
}

/** An applied date filter, such as "Updated after 1 week ago". */
export function CaseDateFilterChip({
  label,
  icon,
  value,
  onChange,
  onRemove,
}: {
  label: string
  icon: ReactNode
  value: CaseDateFilterValue
  onChange: (value: CaseDateFilterValue) => void
  onRemove: () => void
}) {
  const [open, setOpen] = useState(false)
  const isBetween = value.type === "range" && Boolean(value.value.to)
  return (
    <FilterChipFrame
      icon={icon}
      label={label}
      operator={
        <span className="flex h-full shrink-0 items-center border-l border-input px-2 text-muted-foreground">
          {isBetween ? "between" : "after"}
        </span>
      }
      onRemove={onRemove}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`${label} date`}
            className={SEGMENT_CLASS}
          >
            <span className="truncate">{getDateFilterLabel(value)}</span>
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0 shadow-sm" align="start">
          <DateFilterOptions
            value={value}
            onChange={onChange}
            onDone={() => setOpen(false)}
          />
        </PopoverContent>
      </Popover>
    </FilterChipFrame>
  )
}

/**
 * The "Filter" button: pick a property, then its values.
 *
 * Typing in the first step also matches values directly, so "phish" offers
 * the phishing tag without opening Tags first.
 */
export function CaseAddFilterMenu({
  properties,
  showLabel,
}: {
  properties: CaseFilterProperty[]
  /** Show the "Filter" text, for when no filter is applied yet. */
  showLabel: boolean
}) {
  const [open, setOpen] = useState(false)
  const [propertyKey, setPropertyKey] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const property = properties.find((item) => item.key === propertyKey)

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      setPropertyKey(null)
      setSearch("")
    }
  }

  function chooseProperty(key: string) {
    setPropertyKey(key)
    setSearch("")
  }

  let body: ReactNode
  if (property?.kind === "multi") {
    body = (
      <>
        <BackRow label={property.label} onBack={() => setPropertyKey(null)} />
        <FilterOptionList
          label={property.label}
          options={property.options}
          value={property.value}
          onChange={property.onChange}
        />
      </>
    )
  } else if (property?.kind === "date") {
    body = (
      <>
        <BackRow label={property.label} onBack={() => setPropertyKey(null)} />
        <DateFilterOptions
          value={property.value}
          onChange={property.onChange}
          onDone={() => handleOpenChange(false)}
        />
      </>
    )
  } else {
    const query = search.trim()
    body = (
      <Command>
        <CommandInput
          autoFocus
          value={search}
          onValueChange={setSearch}
          placeholder="Filter..."
          className="text-xs"
        />
        <CommandList>
          <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
            No matching filters.
          </CommandEmpty>
          <CommandGroup>
            {properties.map((item) => (
              <CommandItem
                key={item.key}
                value={`property ${item.label}`}
                onSelect={() => chooseProperty(item.key)}
                className="flex items-center gap-2 text-xs"
              >
                {item.icon}
                <span className="truncate">{item.label}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          {query.length > 0 &&
            properties.map((item) =>
              item.kind === "multi" && item.options.length > 0 ? (
                <CommandGroup key={item.key} heading={item.label}>
                  {item.options.map((option) => (
                    <CommandItem
                      key={option.value}
                      value={`${item.label} ${option.label} ${option.value}`}
                      onSelect={() => {
                        if (!item.value.includes(option.value)) {
                          item.onChange([...item.value, option.value])
                        }
                        handleOpenChange(false)
                      }}
                      className="flex items-center gap-2 text-xs"
                    >
                      <OptionIcon option={option} />
                      <span className="truncate" style={option.labelStyle}>
                        {option.label}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              ) : null
            )}
        </CommandList>
      </Command>
    )
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Add filter"
          title="Add filter"
          className={cn(
            "flex h-6 shrink-0 items-center gap-1.5 rounded-md text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            showLabel ? "px-2" : "w-6 justify-center"
          )}
        >
          {showLabel ? (
            <>
              <ListFilterIcon className="size-3.5" />
              <span>Filter</span>
            </>
          ) : (
            <PlusIcon className="size-3.5" />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        className={cn(
          "p-0 shadow-sm",
          property?.kind === "date" ? "w-auto" : "w-60"
        )}
        align="start"
      >
        {body}
      </PopoverContent>
    </Popover>
  )
}

function BackRow({ label, onBack }: { label: string; onBack: () => void }) {
  return (
    <button
      type="button"
      onClick={onBack}
      className="flex w-full items-center gap-2 border-b px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground"
    >
      <ArrowLeftIcon className="size-3.5" />
      {label}
    </button>
  )
}
