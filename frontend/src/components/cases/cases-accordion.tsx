"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  CheckCircleIcon,
  ChevronRightIcon,
  CircleIcon,
  CirclePauseIcon,
  FlagTriangleRightIcon,
  Loader2Icon,
  TrafficConeIcon,
} from "lucide-react"
import type { ComponentType } from "react"
import { useEffect, useMemo, useRef } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseReadMinimal,
  CaseSearchAggregateRead,
  CaseStatus,
  CaseTagRead,
  WorkspaceMember,
} from "@/client"
import { CaseItem } from "@/components/cases/case-item"
import type { FilterMode, SortDirection } from "@/components/cases/cases-header"
import { cn } from "@/lib/utils"

type StatusGroup = "new" | "in_progress" | "on_hold" | "resolved" | "other"

interface StatusGroupConfig {
  label: string
  icon: ComponentType<{ className?: string }>
  statuses: CaseStatus[]
  iconColor: string
}

const STATUS_GROUPS: Record<StatusGroup, StatusGroupConfig> = {
  new: {
    label: "New",
    icon: FlagTriangleRightIcon,
    statuses: ["new"],
    iconColor: "text-yellow-600",
  },
  in_progress: {
    label: "In progress",
    icon: TrafficConeIcon,
    statuses: ["in_progress"],
    iconColor: "text-blue-600",
  },
  on_hold: {
    label: "On hold",
    icon: CirclePauseIcon,
    statuses: ["on_hold"],
    iconColor: "text-orange-600",
  },
  resolved: {
    label: "Resolved",
    icon: CheckCircleIcon,
    statuses: ["resolved", "closed"],
    iconColor: "text-green-600",
  },
  other: {
    label: "Other",
    icon: CircleIcon,
    statuses: ["other", "unknown"],
    iconColor: "text-muted-foreground",
  },
}

const GROUP_ORDER: StatusGroup[] = [
  "new",
  "in_progress",
  "on_hold",
  "resolved",
  "other",
]

interface CasesAccordionProps {
  cases: CaseReadMinimal[]
  selectedId: string | null
  selectedCaseIds: Set<string>
  onSelect: (id: string) => void
  onCheckChange: (id: string, checked: boolean) => void
  onDeleteRequest?: (caseData: CaseReadMinimal) => void
  tags?: CaseTagRead[]
  members?: WorkspaceMember[]
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  prioritySortDirection?: SortDirection
  severitySortDirection?: SortDirection
  assigneeSortDirection?: SortDirection
  tagSortDirection?: SortDirection
  statusFilter?: CaseStatus[]
  statusMode?: FilterMode
  totalFilteredCaseEstimate?: number | null
  stageCounts?: CaseSearchAggregateRead["status_groups"] | null
  isCountsLoading?: boolean
  isCountsFetching?: boolean
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  onLoadMore?: () => void
}

interface VirtualizedGroupRowsProps {
  groupCases: CaseReadMinimal[]
  scrollContainerRef: React.RefObject<HTMLDivElement | null>
  selectedId: string | null
  selectedCaseIds: Set<string>
  onSelect: (id: string) => void
  onCheckChange: (id: string, checked: boolean) => void
  onDeleteRequest?: (caseData: CaseReadMinimal) => void
  tags?: CaseTagRead[]
  members?: WorkspaceMember[]
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
}

function VirtualizedGroupRows({
  groupCases,
  scrollContainerRef,
  selectedId,
  selectedCaseIds,
  onSelect,
  onCheckChange,
  onDeleteRequest,
  tags,
  members,
  dropdownDefinitions,
}: VirtualizedGroupRowsProps) {
  const rowVirtualizer = useVirtualizer({
    count: groupCases.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 44,
    overscan: 8,
    getItemKey: (index) => groupCases[index]?.id ?? index,
  })

  if (groupCases.length === 0) {
    return null
  }

  return (
    <div
      className="relative w-full"
      style={{
        height: `${rowVirtualizer.getTotalSize()}px`,
      }}
    >
      {rowVirtualizer.getVirtualItems().map((virtualItem) => {
        const caseData = groupCases[virtualItem.index]
        if (!caseData) {
          return null
        }

        return (
          <div
            key={virtualItem.key}
            className="absolute left-0 top-0 w-full"
            style={{ transform: `translateY(${virtualItem.start}px)` }}
          >
            <CaseItem
              caseData={caseData}
              isSelected={selectedId === caseData.id}
              isChecked={selectedCaseIds.has(caseData.id)}
              onCheckChange={(checked) => onCheckChange(caseData.id, checked)}
              onClick={() => onSelect(caseData.id)}
              onDeleteRequest={onDeleteRequest}
              tags={tags}
              members={members}
              dropdownDefinitions={dropdownDefinitions}
            />
          </div>
        )
      })}
    </div>
  )
}

export function CasesAccordion({
  cases,
  selectedId,
  selectedCaseIds,
  onSelect,
  onCheckChange,
  onDeleteRequest,
  tags,
  members,
  dropdownDefinitions,
  prioritySortDirection,
  severitySortDirection,
  assigneeSortDirection,
  tagSortDirection,
  statusFilter = [],
  statusMode = "include",
  totalFilteredCaseEstimate = null,
  stageCounts = null,
  isCountsLoading = false,
  isCountsFetching = false,
  hasNextPage = false,
  isFetchingNextPage = false,
  onLoadMore,
}: CasesAccordionProps) {
  const hasExplicitSort =
    prioritySortDirection ||
    severitySortDirection ||
    assigneeSortDirection ||
    tagSortDirection

  const groupedCases = useMemo(() => {
    const groups: Record<StatusGroup, CaseReadMinimal[]> = {
      new: [],
      in_progress: [],
      on_hold: [],
      resolved: [],
      other: [],
    }

    for (const caseData of cases) {
      let matched = false
      for (const [groupKey, config] of Object.entries(STATUS_GROUPS)) {
        if (config.statuses.includes(caseData.status)) {
          groups[groupKey as StatusGroup].push(caseData)
          matched = true
          break
        }
      }
      if (!matched) {
        groups.other.push(caseData)
      }
    }

    if (!hasExplicitSort) {
      for (const groupKey of Object.keys(groups) as StatusGroup[]) {
        groups[groupKey].sort(
          (a, b) =>
            new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        )
      }
    }

    return groups
  }, [cases, hasExplicitSort])

  const defaultExpandedGroups = useMemo(() => {
    return GROUP_ORDER.filter((group) => groupedCases[group].length > 0)
  }, [groupedCases])

  const singleFilteredGroup = useMemo<StatusGroup | null>(() => {
    if (statusMode !== "include" || statusFilter.length === 0) {
      return null
    }

    const matchingGroups = GROUP_ORDER.filter((group) =>
      statusFilter.every((status) =>
        STATUS_GROUPS[group].statuses.includes(status)
      )
    )

    return matchingGroups.length === 1 ? matchingGroups[0] : null
  }, [statusFilter, statusMode])

  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!hasNextPage || !onLoadMore) {
      return
    }

    const root = scrollContainerRef.current
    const target = loadMoreRef.current
    if (!root || !target) {
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const isVisible = entries.some((entry) => entry.isIntersecting)
        if (isVisible && !isFetchingNextPage) {
          onLoadMore()
        }
      },
      {
        root,
        rootMargin: "300px 0px",
      }
    )

    observer.observe(target)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, onLoadMore])

  return (
    <div ref={scrollContainerRef} className="h-full overflow-auto">
      <AccordionPrimitive.Root
        type="multiple"
        defaultValue={defaultExpandedGroups}
        className="w-full"
      >
        {GROUP_ORDER.map((groupKey) => {
          const config = STATUS_GROUPS[groupKey]
          const groupCases = groupedCases[groupKey]
          const StatusIcon = config.icon
          const hasGlobalCount = typeof stageCounts?.[groupKey] === "number"
          const groupCount =
            singleFilteredGroup === groupKey &&
            typeof totalFilteredCaseEstimate === "number"
              ? totalFilteredCaseEstimate
              : hasGlobalCount
                ? stageCounts[groupKey]
                : groupCases.length

          return (
            <AccordionPrimitive.Item
              key={groupKey}
              value={groupKey}
              className="group/accordion border-b border-border/50"
              data-status={groupKey}
            >
              <AccordionPrimitive.Header className="flex">
                <AccordionPrimitive.Trigger
                  className={cn(
                    "flex w-full items-center gap-1 border-l-2 border-l-transparent py-1.5 pl-[10px] pr-3 text-left transition-colors",
                    "hover:bg-muted/50",
                    "[&[data-state=open]_.chevron]:rotate-90",
                    "data-[state=open]:border-l-current",
                    groupKey === "new" &&
                      "data-[state=open]:border-l-yellow-600 data-[state=open]:bg-yellow-600/[0.03] dark:data-[state=open]:bg-yellow-600/[0.08]",
                    groupKey === "in_progress" &&
                      "data-[state=open]:border-l-blue-600 data-[state=open]:bg-blue-600/[0.03] dark:data-[state=open]:bg-blue-600/[0.08]",
                    groupKey === "on_hold" &&
                      "data-[state=open]:border-l-orange-600 data-[state=open]:bg-orange-600/[0.03] dark:data-[state=open]:bg-orange-600/[0.08]",
                    groupKey === "resolved" &&
                      "data-[state=open]:border-l-green-600 data-[state=open]:bg-green-600/[0.03] dark:data-[state=open]:bg-green-600/[0.08]",
                    groupKey === "other" &&
                      "data-[state=open]:border-l-muted-foreground data-[state=open]:bg-muted/50"
                  )}
                >
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                    <ChevronRightIcon className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <StatusIcon
                      className={cn("size-4 shrink-0", config.iconColor)}
                    />
                    <span className="text-xs font-medium">{config.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {groupCount}
                    </span>
                    {(isCountsLoading || isCountsFetching) && (
                      <Loader2Icon className="size-3 animate-spin text-muted-foreground" />
                    )}
                  </div>
                </AccordionPrimitive.Trigger>
              </AccordionPrimitive.Header>
              <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                <div className="ml-[18px]">
                  <VirtualizedGroupRows
                    groupCases={groupCases}
                    scrollContainerRef={scrollContainerRef}
                    selectedId={selectedId}
                    selectedCaseIds={selectedCaseIds}
                    onSelect={onSelect}
                    onCheckChange={onCheckChange}
                    onDeleteRequest={onDeleteRequest}
                    tags={tags}
                    members={members}
                    dropdownDefinitions={dropdownDefinitions}
                  />
                </div>
              </AccordionPrimitive.Content>
            </AccordionPrimitive.Item>
          )
        })}
      </AccordionPrimitive.Root>

      {(hasNextPage || isFetchingNextPage) && (
        <div
          ref={loadMoreRef}
          className="flex h-10 items-center justify-center text-xs text-muted-foreground"
        >
          {isFetchingNextPage ? "Loading more cases..." : "Scroll to load more"}
        </div>
      )}
    </div>
  )
}
