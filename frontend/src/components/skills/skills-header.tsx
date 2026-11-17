"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import {
  CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FolderIcon,
  ListIcon,
  MousePointerClickIcon,
  TypeIcon,
} from "lucide-react"
import { FilterMultiSelect } from "@/components/filters/filter-multi-select"
import { ListHeaderSearch } from "@/components/list-header-search"
import {
  type ListSortFieldOption,
  ListSortSelect,
  type ListSortValue,
} from "@/components/list-sort-select"
import type { SkillAgentFilter } from "@/components/skills/use-skill-agent-filter"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export type SkillsSortField = "updated_at" | "created_at" | "name"
export type SkillsSortValue = ListSortValue<SkillsSortField>

export type SkillsViewMode = "folders" | "list"

export const DEFAULT_SKILL_SORT: SkillsSortValue = {
  field: "updated_at",
  direction: "desc",
}

const SORT_FIELD_OPTIONS: Array<ListSortFieldOption<SkillsSortField>> = [
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

interface SkillsHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  sortBy: SkillsSortValue
  onSortByChange: (value: SkillsSortValue) => void
  agentFilter: SkillAgentFilter
  view: SkillsViewMode
  onViewChange: (view: SkillsViewMode) => void
  viewSwitchEnabled?: boolean
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
 * with a slimmed control set: search, sort, and an agent filter.
 */
export function SkillsHeader({
  searchQuery,
  onSearchChange,
  sortBy,
  onSortByChange,
  agentFilter,
  view,
  onViewChange,
  viewSwitchEnabled = true,
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
  const hasFilters =
    searchQuery.trim().length > 0 ||
    !isDefaultSort(sortBy) ||
    agentFilter.active

  const handleReset = () => {
    onSearchChange("")
    onSortByChange(DEFAULT_SKILL_SORT)
    if (agentFilter.active) agentFilter.setAgentIds([])
  }

  return (
    <div className="shrink-0 border-b">
      <header className="flex h-10 items-center gap-3 border-b pl-3 pr-4">
        <ListHeaderSearch
          placeholder="Search skills..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
        />

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
        {viewSwitchEnabled ? (
          <Select
            value={view}
            onValueChange={(nextView) =>
              onViewChange(nextView as SkillsViewMode)
            }
          >
            <SelectTrigger className="h-6 w-[105px] gap-1.5 rounded-md px-2 text-xs font-medium">
              {view === "folders" ? (
                <FolderIcon className="size-3.5 text-muted-foreground" />
              ) : (
                <ListIcon className="size-3.5 text-muted-foreground" />
              )}
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="list">List</SelectItem>
              <SelectItem value="folders">Folders</SelectItem>
            </SelectContent>
          </Select>
        ) : null}
        <ListSortSelect
          value={sortBy}
          onChange={onSortByChange}
          options={SORT_FIELD_OPTIONS}
        />
        <FilterMultiSelect
          placeholder="Agents"
          icon={MousePointerClickIcon}
          value={agentFilter.agentIds}
          options={agentFilter.options}
          onChange={agentFilter.setAgentIds}
          mode="include"
          onModeChange={() => {}}
          allowExclude={false}
          emptyMessage={agentFilter.emptyMessage}
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
