"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  CheckCircleIcon,
  ChevronRightIcon,
  CircleHelpIcon,
  CircleIcon,
  CirclePauseIcon,
  FlagTriangleRightIcon,
  Loader2Icon,
  TrafficConeIcon,
} from "lucide-react"
import type { ComponentType } from "react"
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
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

type StatusGroup =
  | "new"
  | "in_progress"
  | "on_hold"
  | "resolved"
  | "closed"
  | "other"
  | "unknown"

interface StatusGroupConfig {
  label: string
  icon: ComponentType<{ className?: string }>
  statuses: CaseStatus[]
  iconColor: string
  aggregateKey?: keyof CaseSearchAggregateRead["status_groups"]
}

const STATUS_GROUPS: Record<StatusGroup, StatusGroupConfig> = {
  new: {
    label: "New",
    icon: FlagTriangleRightIcon,
    statuses: ["new"],
    iconColor: "text-yellow-600",
    aggregateKey: "new",
  },
  in_progress: {
    label: "In progress",
    icon: TrafficConeIcon,
    statuses: ["in_progress"],
    iconColor: "text-blue-600",
    aggregateKey: "in_progress",
  },
  on_hold: {
    label: "On hold",
    icon: CirclePauseIcon,
    statuses: ["on_hold"],
    iconColor: "text-orange-600",
    aggregateKey: "on_hold",
  },
  resolved: {
    label: "Resolved",
    icon: CheckCircleIcon,
    statuses: ["resolved"],
    iconColor: "text-green-600",
    aggregateKey: "resolved",
  },
  closed: {
    label: "Closed",
    icon: CheckCircleIcon,
    statuses: ["closed"],
    iconColor: "text-violet-600",
  },
  other: {
    label: "Other",
    icon: CircleIcon,
    statuses: ["other"],
    iconColor: "text-muted-foreground",
    aggregateKey: "other",
  },
  unknown: {
    label: "Unknown",
    icon: CircleHelpIcon,
    statuses: ["unknown"],
    iconColor: "text-slate-600",
  },
}

const GROUP_ORDER: StatusGroup[] = [
  "new",
  "in_progress",
  "on_hold",
  "resolved",
  "closed",
  "other",
  "unknown",
]

function isStatusGroup(value: string): value is StatusGroup {
  return value in STATUS_GROUPS
}

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
  const groupContainerRef = useRef<HTMLDivElement | null>(null)
  const [scrollMargin, setScrollMargin] = useState(0)

  useLayoutEffect(() => {
    const scrollElement = scrollContainerRef.current
    const groupElement = groupContainerRef.current
    if (!scrollElement || !groupElement) {
      return
    }

    let frameId = 0

    const updateScrollMargin = () => {
      const currentScrollElement = scrollContainerRef.current
      const currentGroupElement = groupContainerRef.current
      if (!currentScrollElement || !currentGroupElement) {
        return
      }

      const nextMargin =
        currentGroupElement.getBoundingClientRect().top -
        currentScrollElement.getBoundingClientRect().top +
        currentScrollElement.scrollTop

      setScrollMargin((prev) =>
        Math.abs(prev - nextMargin) < 0.5 ? prev : nextMargin
      )
    }

    const scheduleUpdate = () => {
      if (frameId) {
        return
      }

      frameId = window.requestAnimationFrame(() => {
        frameId = 0
        updateScrollMargin()
      })
    }

    updateScrollMargin()

    const resizeObserver = new ResizeObserver(scheduleUpdate)
    resizeObserver.observe(groupElement)
    const accordionRoot = scrollElement.firstElementChild
    if (accordionRoot instanceof HTMLElement) {
      resizeObserver.observe(accordionRoot)
    }

    scrollElement.addEventListener("scroll", scheduleUpdate, { passive: true })
    window.addEventListener("resize", scheduleUpdate)

    return () => {
      resizeObserver.disconnect()
      scrollElement.removeEventListener("scroll", scheduleUpdate)
      window.removeEventListener("resize", scheduleUpdate)
      if (frameId) {
        window.cancelAnimationFrame(frameId)
      }
    }
  }, [groupCases.length, scrollContainerRef])

  const rowVirtualizer = useVirtualizer({
    count: groupCases.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 44,
    overscan: 8,
    scrollMargin,
    getItemKey: (index) => groupCases[index]?.id ?? index,
  })

  if (groupCases.length === 0) {
    return null
  }

  return (
    <div
      ref={groupContainerRef}
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
            style={{
              transform: `translateY(${virtualItem.start - scrollMargin}px)`,
            }}
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
  const [expandedGroups, setExpandedGroups] = useState<StatusGroup[]>([])
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
      closed: [],
      other: [],
      unknown: [],
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
  const hasExpandedGroups = expandedGroups.length > 0

  useEffect(() => {
    if (!hasExpandedGroups || !hasNextPage || !onLoadMore) {
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
  }, [hasExpandedGroups, hasNextPage, isFetchingNextPage, onLoadMore])

  return (
    <div ref={scrollContainerRef} className="h-full overflow-auto">
      <AccordionPrimitive.Root
        type="multiple"
        value={expandedGroups}
        onValueChange={(value) => {
          setExpandedGroups(value.filter(isStatusGroup))
        }}
        className="w-full"
      >
        {GROUP_ORDER.map((groupKey) => {
          const config = STATUS_GROUPS[groupKey]
          const groupCases = groupedCases[groupKey]
          const StatusIcon = config.icon
          const aggregateKey = config.aggregateKey
          const globalCount = aggregateKey ? stageCounts?.[aggregateKey] : null
          const hasGlobalCount = typeof globalCount === "number"
          const groupCount =
            singleFilteredGroup === groupKey &&
            typeof totalFilteredCaseEstimate === "number"
              ? totalFilteredCaseEstimate
              : hasGlobalCount
                ? globalCount
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
                    groupKey === "closed" &&
                      "data-[state=open]:border-l-violet-600 data-[state=open]:bg-violet-600/[0.03] dark:data-[state=open]:bg-violet-600/[0.08]",
                    groupKey === "other" &&
                      "data-[state=open]:border-l-muted-foreground data-[state=open]:bg-muted/50",
                    groupKey === "unknown" &&
                      "data-[state=open]:border-l-slate-600 data-[state=open]:bg-slate-600/[0.03] dark:data-[state=open]:bg-slate-600/[0.08]"
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

      {hasExpandedGroups && (hasNextPage || isFetchingNextPage) && (
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
