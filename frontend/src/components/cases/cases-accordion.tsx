"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import {
  CheckCircleIcon,
  ChevronRightIcon,
  CircleIcon,
  CirclePauseIcon,
  FlagTriangleRightIcon,
  TrafficConeIcon,
} from "lucide-react"
import { useMemo } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseReadMinimal,
  CaseStatus,
  CaseTagRead,
  WorkspaceMember,
} from "@/client"
import { CaseItem } from "@/components/cases/case-item"
import type { SortDirection } from "@/components/cases/cases-header"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { CasesRecencySort } from "@/hooks/use-cases"
import { cn } from "@/lib/utils"

// Define the status groups we want to display
type StatusGroup = "new" | "in_progress" | "on_hold" | "resolved" | "other"

interface StatusGroupConfig {
  label: string
  icon: React.ComponentType<{ className?: string }>
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

// Order in which groups should appear
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
  /** When any sort direction is active, preserve the order from the hook */
  prioritySortDirection?: SortDirection
  severitySortDirection?: SortDirection
  assigneeSortDirection?: SortDirection
  tagSortDirection?: SortDirection
  updatedAtSort?: CasesRecencySort
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
  updatedAtSort = "desc",
}: CasesAccordionProps) {
  // Check if any explicit sort is active (from the header)
  const hasExplicitSort =
    prioritySortDirection ||
    severitySortDirection ||
    assigneeSortDirection ||
    tagSortDirection ||
    updatedAtSort === "asc"

  // Group cases by status category
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
      // If no match, put in "other"
      if (!matched) {
        groups.other.push(caseData)
      }
    }

    // Only apply default updated_at sorting when no explicit sort is active
    // When explicit sorting is applied, preserve the order from the hook
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

  // Calculate which groups have items and should be expanded by default
  const defaultExpandedGroups = useMemo(() => {
    return GROUP_ORDER.filter((group) => groupedCases[group].length > 0)
  }, [groupedCases])

  return (
    <ScrollArea className="h-full">
      <AccordionPrimitive.Root
        type="multiple"
        defaultValue={defaultExpandedGroups}
        className="w-full"
      >
        {GROUP_ORDER.map((groupKey) => {
          const config = STATUS_GROUPS[groupKey]
          const groupCases = groupedCases[groupKey]
          const StatusIcon = config.icon

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
                    // Use pl-[10px] to compensate for border-l-2 (2px) so total left offset matches px-3 (12px)
                    "flex w-full items-center gap-1 border-l-2 border-l-transparent py-1.5 pl-[10px] pr-3 text-left transition-colors",
                    "hover:bg-muted/50",
                    "[&[data-state=open]_.chevron]:rotate-90",
                    // Tint backgrounds when open
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
                  {/* Match SidebarTrigger dimensions (h-7 w-7) for alignment */}
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                    <ChevronRightIcon className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <StatusIcon
                      className={cn("size-4 shrink-0", config.iconColor)}
                    />
                    <span className="text-xs font-medium">{config.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {groupCases.length}
                    </span>
                  </div>
                </AccordionPrimitive.Trigger>
              </AccordionPrimitive.Header>
              <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                <div className="ml-[18px]">
                  {groupCases.map((caseData) => (
                    <CaseItem
                      key={caseData.id}
                      caseData={caseData}
                      isSelected={selectedId === caseData.id}
                      isChecked={selectedCaseIds.has(caseData.id)}
                      onCheckChange={(checked) =>
                        onCheckChange(caseData.id, checked)
                      }
                      onClick={() => onSelect(caseData.id)}
                      onDeleteRequest={onDeleteRequest}
                      tags={tags}
                      members={members}
                      dropdownDefinitions={dropdownDefinitions}
                    />
                  ))}
                </div>
              </AccordionPrimitive.Content>
            </AccordionPrimitive.Item>
          )
        })}
      </AccordionPrimitive.Root>
    </ScrollArea>
  )
}
