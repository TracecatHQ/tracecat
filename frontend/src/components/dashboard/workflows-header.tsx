"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CalendarIcon,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FolderIcon,
  ListIcon,
  SearchIcon,
  TagIcon,
  TypeIcon,
} from "lucide-react"
import { type ComponentType, type ReactNode, useMemo, useState } from "react"
import type { TagRead } from "@/client"
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
export type WorkflowsSortField = "updated_at" | "created_at" | "name"
export type WorkflowsSortDirection = "asc" | "desc"

export interface WorkflowsSortValue {
  field: WorkflowsSortField
  direction: WorkflowsSortDirection
}

export const DEFAULT_WORKFLOW_SORT: WorkflowsSortValue = {
  field: "updated_at",
  direction: "desc",
}

const SORT_FIELD_OPTIONS: Array<{
  value: WorkflowsSortField
  label: string
  icon: ComponentType<{ className?: string }>
}> = [
  { value: "updated_at", label: "Updated", icon: Clock3 },
  { value: "created_at", label: "Created", icon: CalendarIcon },
  { value: "name", label: "Name", icon: TypeIcon },
]

const LIMIT_OPTIONS = [10, 20, 50]

function isDefaultSort(value: WorkflowsSortValue): boolean {
  return (
    value.field === DEFAULT_WORKFLOW_SORT.field &&
    value.direction === DEFAULT_WORKFLOW_SORT.direction
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

interface SortBySelectProps {
  value: WorkflowsSortValue
  onChange: (next: WorkflowsSortValue) => void
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
            field: nextField as WorkflowsSortField,
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

interface WorkflowsHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  view: WorkflowsViewMode
  onViewChange: (view: WorkflowsViewMode) => void
  tags?: TagRead[]
  tagFilter: string[]
  onTagChange: (value: string[]) => void
  sortBy: WorkflowsSortValue
  onSortByChange: (value: WorkflowsSortValue) => void
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
}: WorkflowsHeaderProps) {
  const hasFilters =
    searchQuery.trim().length > 0 ||
    tagFilter.length > 0 ||
    !isDefaultSort(sortBy)

  const handleReset = () => {
    onSearchChange("")
    onTagChange([])
    onSortByChange(DEFAULT_WORKFLOW_SORT)
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
