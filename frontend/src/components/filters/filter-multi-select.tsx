"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  Check,
  ChevronDown,
  Minus,
  Plus,
} from "lucide-react"
import {
  type ComponentType,
  type CSSProperties,
  type ReactNode,
  useMemo,
  useState,
} from "react"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export interface FilterOption<T extends string = string> {
  value: T
  label: string
  icon?: ComponentType<{ className?: string; style?: CSSProperties }>
  iconClassName?: string
  iconStyle?: CSSProperties
  labelClassName?: string
  labelStyle?: CSSProperties
  /** Custom render function for icons that need dynamic content (e.g., avatars) */
  renderIcon?: () => ReactNode
}

export type FilterMode = "include" | "exclude"
export type SortDirection = "asc" | "desc" | null

interface FilterMultiSelectProps<T extends string> {
  placeholder: string
  icon?: ComponentType<{ className?: string }>
  /** Alternative to `icon` — render a custom trigger icon node */
  renderTriggerIcon?: () => ReactNode
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
  allowExclude?: boolean
  modeLabel?: string
}

export function FilterMultiSelect<T extends string>({
  placeholder,
  icon: Icon,
  renderTriggerIcon,
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
  allowExclude = true,
  modeLabel,
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
          {renderTriggerIcon
            ? renderTriggerIcon()
            : Icon && <Icon className="size-3.5 text-muted-foreground" />}
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
            {modeLabel ?? (mode === "exclude" ? "Excluding" : "Including")}
          </span>
          <div className="flex items-center gap-0.5">
            {(allowExclude
              ? (["include", "exclude"] as FilterMode[])
              : (["include"] as FilterMode[])
            ).map((option) => (
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
        {selectedCount > 0 && (
          <div className="border-t p-1">
            <button
              type="button"
              className="flex h-7 w-full items-center justify-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={() => onChange([])}
            >
              Clear selection
              <Cross2Icon className="size-3" />
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
