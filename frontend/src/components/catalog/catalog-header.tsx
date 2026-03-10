"use client"

import type { LucideIcon } from "lucide-react"
import { Search } from "lucide-react"
import type { ReactNode } from "react"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export interface CatalogHeaderPillOption<TValue extends string = string> {
  value: TValue
  label: string
  icon: LucideIcon
}

export interface CatalogHeaderSelectOption {
  value: string
  label: string
  icon?: LucideIcon
}

export interface CatalogHeaderSelectFilter {
  key: string
  value: string
  onValueChange: (value: string) => void
  options: CatalogHeaderSelectOption[]
  placeholder: string
  allValue?: string
  widthClassName?: string
}

interface CatalogHeaderProps<TPillValue extends string = string> {
  searchQuery: string
  onSearchChange: (query: string) => void
  searchPlaceholder: string
  pillFilters?: Array<CatalogHeaderPillOption<TPillValue>>
  activePillFilters?: TPillValue[]
  onPillFilterToggle?: (filter: TPillValue) => void
  selectFilters?: Array<CatalogHeaderSelectFilter>
  displayCount?: number
  countLabel?: ReactNode
}

const filterButtonClassName =
  "flex h-6 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors hover:bg-muted/50"

export function CatalogHeader<TPillValue extends string = string>({
  searchQuery,
  onSearchChange,
  searchPlaceholder,
  pillFilters = [],
  activePillFilters = [],
  onPillFilterToggle,
  selectFilters = [],
  displayCount,
  countLabel,
}: CatalogHeaderProps<TPillValue>) {
  const hasFilterRow = pillFilters.length > 0 || selectFilters.length > 0

  return (
    <div className="w-full shrink-0 border-b">
      <header
        className={cn(
          "flex h-10 w-full items-center pl-3 pr-4",
          hasFilterRow && "border-b"
        )}
      >
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center">
            <Search className="size-4 text-muted-foreground" />
          </div>
          <Input
            type="text"
            placeholder={searchPlaceholder}
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
            className={cn(
              "h-7 w-48 border-none bg-transparent p-0",
              "text-sm",
              "shadow-none outline-none",
              "placeholder:text-muted-foreground",
              "focus-visible:ring-0 focus-visible:ring-offset-0"
            )}
          />
        </div>

        {displayCount !== undefined && countLabel ? (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {displayCount} {countLabel}
            </span>
          </div>
        ) : null}
      </header>

      {hasFilterRow ? (
        <div className="flex w-full flex-wrap items-center gap-2 py-2 pl-3 pr-4">
          {pillFilters.map((option) => {
            const isActive = activePillFilters.includes(option.value)
            const Icon = option.icon
            return (
              <button
                key={option.value}
                type="button"
                className={cn(
                  filterButtonClassName,
                  isActive && "border-primary/50 bg-primary/5"
                )}
                aria-pressed={isActive}
                onClick={() => onPillFilterToggle?.(option.value)}
              >
                <Icon className="size-3.5 text-muted-foreground" />
                {option.label}
              </button>
            )
          })}

          {selectFilters.map((filter) => (
            <Select
              key={filter.key}
              value={filter.value}
              onValueChange={filter.onValueChange}
            >
              <SelectTrigger
                className={cn(
                  "h-6 rounded-md border border-input bg-transparent px-2 text-xs font-medium hover:bg-muted/50",
                  filter.widthClassName ?? "w-[170px]",
                  filter.allValue !== undefined &&
                    filter.value !== filter.allValue &&
                    "border-primary/50 bg-primary/5"
                )}
              >
                <SelectValue placeholder={filter.placeholder} />
              </SelectTrigger>
              <SelectContent>
                {filter.options.map((option) => {
                  const Icon = option.icon
                  return (
                    <SelectItem key={option.value} value={option.value}>
                      {Icon ? (
                        <span className="flex items-center gap-2">
                          <Icon className="size-3.5 text-muted-foreground" />
                          {option.label}
                        </span>
                      ) : (
                        option.label
                      )}
                    </SelectItem>
                  )
                })}
              </SelectContent>
            </Select>
          ))}
        </div>
      ) : null}
    </div>
  )
}
