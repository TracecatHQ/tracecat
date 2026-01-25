"use client"

import {
  CalendarIcon,
  CheckIcon,
  ClockIcon,
  FilterIcon,
  HashIcon,
  LayersIcon,
  SearchIcon,
} from "lucide-react"
import type { AgentSessionEntity } from "@/client"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import type { DateFilterValue } from "@/hooks/use-inbox"
import { cn } from "@/lib/utils"

const ENTITY_TYPE_OPTIONS: Array<{
  value: AgentSessionEntity | "all"
  label: string
}> = [
  { value: "all", label: "All types" },
  { value: "case", label: "Case" },
  { value: "agent_preset", label: "Agent preset" },
  { value: "workflow", label: "Workflow" },
  { value: "copilot", label: "Copilot" },
]

const LIMIT_OPTIONS = [
  { value: 10, label: "10" },
  { value: 20, label: "20" },
  { value: 50, label: "50" },
]

const DATE_FILTER_OPTIONS: Array<{
  value: DateFilterValue
  label: string
}> = [
  { value: null, label: "Any time" },
  { value: "1d", label: "1 day ago" },
  { value: "3d", label: "3 days ago" },
  { value: "1w", label: "1 week ago" },
  { value: "1m", label: "1 month ago" },
]

interface InboxHeaderProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  entityType: AgentSessionEntity | "all"
  onEntityTypeChange: (type: AgentSessionEntity | "all") => void
  limit: number
  onLimitChange: (limit: number) => void
  updatedAfter: DateFilterValue
  onUpdatedAfterChange: (value: DateFilterValue) => void
  createdAfter: DateFilterValue
  onCreatedAfterChange: (value: DateFilterValue) => void
}

export function InboxHeader({
  searchQuery,
  onSearchChange,
  entityType,
  onEntityTypeChange,
  limit,
  onLimitChange,
  updatedAfter,
  onUpdatedAfterChange,
  createdAfter,
  onCreatedAfterChange,
}: InboxHeaderProps) {
  // Count only non-default filters (don't count limit since it always has a value)
  const activeFilterCount =
    (entityType !== "all" ? 1 : 0) +
    (updatedAfter !== null ? 1 : 0) +
    (createdAfter !== null ? 1 : 0)

  return (
    <header className="flex h-10 shrink-0 items-center border-b px-3">
      {/* Left section: Search input with magnifying glass */}
      <div className="flex items-center gap-3 min-w-0">
        {/* Match SidebarTrigger dimensions (h-7 w-7) for alignment */}
        <div className="flex h-7 w-7 shrink-0 items-center justify-center">
          <SearchIcon className="size-4 text-muted-foreground" />
        </div>
        <Input
          type="text"
          placeholder="Search sessions..."
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

      {/* Middle section: spacer */}
      <div className="flex-1" />

      {/* Right section: Filter dropdown */}
      <div className="flex items-center shrink-0">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                "flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors",
                "hover:bg-muted text-foreground"
              )}
            >
              <FilterIcon className="size-3.5" />
              <span>Filter</span>
              {activeFilterCount > 0 && (
                <span className="flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
                  {activeFilterCount}
                </span>
              )}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            {/* Type filter submenu */}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="flex items-center gap-2">
                <LayersIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="shrink-0">Type</span>
                {entityType !== "all" && (
                  <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                    {ENTITY_TYPE_OPTIONS.find((o) => o.value === entityType)
                      ?.label ?? entityType}
                  </span>
                )}
              </DropdownMenuSubTrigger>
              <DropdownMenuPortal>
                <DropdownMenuSubContent className="w-40">
                  {ENTITY_TYPE_OPTIONS.map((option) => (
                    <DropdownMenuItem
                      key={option.value}
                      onClick={() => onEntityTypeChange(option.value)}
                      className="flex items-center justify-between"
                    >
                      <span>{option.label}</span>
                      {entityType === option.value && (
                        <CheckIcon className="size-4" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuPortal>
            </DropdownMenuSub>

            {/* Updated filter submenu */}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="flex items-center gap-2">
                <ClockIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="shrink-0">Updated</span>
                {updatedAfter && (
                  <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                    {DATE_FILTER_OPTIONS.find((o) => o.value === updatedAfter)
                      ?.label ?? updatedAfter}
                  </span>
                )}
              </DropdownMenuSubTrigger>
              <DropdownMenuPortal>
                <DropdownMenuSubContent className="w-32">
                  {DATE_FILTER_OPTIONS.map((option) => (
                    <DropdownMenuItem
                      key={option.value ?? "any"}
                      onClick={() => onUpdatedAfterChange(option.value)}
                      className="flex items-center justify-between"
                    >
                      <span>{option.label}</span>
                      {updatedAfter === option.value && (
                        <CheckIcon className="size-4" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuPortal>
            </DropdownMenuSub>

            {/* Created filter submenu */}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="flex items-center gap-2">
                <CalendarIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="shrink-0">Created</span>
                {createdAfter && (
                  <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                    {DATE_FILTER_OPTIONS.find((o) => o.value === createdAfter)
                      ?.label ?? createdAfter}
                  </span>
                )}
              </DropdownMenuSubTrigger>
              <DropdownMenuPortal>
                <DropdownMenuSubContent className="w-32">
                  {DATE_FILTER_OPTIONS.map((option) => (
                    <DropdownMenuItem
                      key={option.value ?? "any"}
                      onClick={() => onCreatedAfterChange(option.value)}
                      className="flex items-center justify-between"
                    >
                      <span>{option.label}</span>
                      {createdAfter === option.value && (
                        <CheckIcon className="size-4" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuPortal>
            </DropdownMenuSub>

            {/* Limit filter submenu */}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="flex items-center gap-2">
                <HashIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="shrink-0">Limit</span>
                <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-muted-foreground">
                  {limit}
                </span>
              </DropdownMenuSubTrigger>
              <DropdownMenuPortal>
                <DropdownMenuSubContent className="w-24">
                  {LIMIT_OPTIONS.map((option) => (
                    <DropdownMenuItem
                      key={option.value}
                      onClick={() => onLimitChange(option.value)}
                      className="flex items-center justify-between"
                    >
                      <span>{option.label}</span>
                      {limit === option.value && (
                        <CheckIcon className="size-4" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuPortal>
            </DropdownMenuSub>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}
