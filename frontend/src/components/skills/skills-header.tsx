"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Clock3,
  SearchIcon,
  TypeIcon,
} from "lucide-react"
import type { ComponentType } from "react"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export type SkillsSortField = "updated_at" | "created_at" | "name"
export type SkillsSortDirection = "asc" | "desc"

export interface SkillsSortValue {
  field: SkillsSortField
  direction: SkillsSortDirection
}

export const DEFAULT_SKILL_SORT: SkillsSortValue = {
  field: "updated_at",
  direction: "desc",
}

const SORT_FIELD_OPTIONS: Array<{
  value: SkillsSortField
  label: string
  icon: ComponentType<{ className?: string }>
}> = [
  { value: "updated_at", label: "Updated", icon: Clock3 },
  { value: "created_at", label: "Created", icon: CalendarIcon },
  { value: "name", label: "Name", icon: TypeIcon },
]

const LIMIT_OPTIONS = [10, 20, 50]

function isDefaultSort(value: SkillsSortValue): boolean {
  return (
    value.field === DEFAULT_SKILL_SORT.field &&
    value.direction === DEFAULT_SKILL_SORT.direction
  )
}

interface SortBySelectProps {
  value: SkillsSortValue
  onChange: (next: SkillsSortValue) => void
}

function SortBySelect({ value, onChange }: SortBySelectProps) {
  const selectedLabel =
    SORT_FIELD_OPTIONS.find((option) => option.value === value.field)?.label ??
    "Updated"

  return (
    <div className="inline-flex items-center rounded-md border border-input bg-transparent">
      <Select
        value={value.field}
        onValueChange={(nextField) =>
          onChange({
            field: nextField as SkillsSortField,
            direction: value.direction,
          })
        }
      >
        <SelectTrigger className="h-6 w-[145px] rounded-r-none border-0 bg-transparent px-2 text-xs font-medium shadow-none focus:ring-0">
          <span className="text-muted-foreground">Sort by</span>
          <span className="text-foreground">{selectedLabel}</span>
        </SelectTrigger>
        <SelectContent align="start">
          {SORT_FIELD_OPTIONS.map((option) => {
            const Icon = option.icon
            return (
              <SelectItem key={option.value} value={option.value}>
                <span className="flex items-center gap-2">
                  <Icon className="size-3.5 text-muted-foreground" />
                  <span>{option.label}</span>
                </span>
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
      <button
        type="button"
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

interface SkillsHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  sortBy: SkillsSortValue
  onSortByChange: (value: SkillsSortValue) => void
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

/**
 * List header for the skills dashboard. Mirrors the workflows header layout
 * with a slimmed control set (no view toggle, tags, webhooks, schedules, or
 * case-trigger filters).
 */
export function SkillsHeader({
  searchQuery,
  onSearchChange,
  sortBy,
  onSortByChange,
  totalCount,
  countLabel,
  limit,
  onLimitChange,
  hasPreviousPage,
  hasNextPage,
  onPreviousPage,
  onNextPage,
  isPaginationLoading = false,
}: SkillsHeaderProps) {
  const hasFilters = searchQuery.trim().length > 0 || !isDefaultSort(sortBy)

  const handleReset = () => {
    onSearchChange("")
    onSortByChange(DEFAULT_SKILL_SORT)
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
            placeholder="Search skills..."
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
        <SortBySelect value={sortBy} onChange={onSortByChange} />

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
