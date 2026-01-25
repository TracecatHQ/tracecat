"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import {
  CheckCircle2Icon,
  ChevronRightIcon,
  CirclePauseIcon,
  LoaderCircleIcon,
  XCircleIcon,
} from "lucide-react"
import { useMemo } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { AgentDerivedStatus, AgentSessionWithStatus } from "@/lib/agents"
import { cn } from "@/lib/utils"
import { ActivityItem } from "./activity-item"

// Define the status groups we want to display
type StatusGroup = "review_required" | "running" | "error" | "completed"

interface StatusGroupConfig {
  label: string
  icon: React.ComponentType<{ className?: string }>
  statuses: AgentDerivedStatus[]
  iconColor: string
}

const STATUS_GROUPS: Record<StatusGroup, StatusGroupConfig> = {
  review_required: {
    label: "Review required",
    icon: CirclePauseIcon,
    statuses: ["PENDING_APPROVAL"],
    iconColor: "text-primary",
  },
  running: {
    label: "In progress",
    icon: LoaderCircleIcon,
    statuses: ["RUNNING", "CONTINUED_AS_NEW"],
    iconColor: "text-muted-foreground",
  },
  error: {
    label: "Error",
    icon: XCircleIcon,
    statuses: ["FAILED", "TIMED_OUT", "TERMINATED"],
    iconColor: "text-red-600",
  },
  completed: {
    label: "Completed",
    icon: CheckCircle2Icon,
    statuses: ["COMPLETED", "CANCELED", "UNKNOWN"],
    iconColor: "text-green-600",
  },
}

// Order in which groups should appear
const GROUP_ORDER: StatusGroup[] = [
  "review_required",
  "running",
  "error",
  "completed",
]

interface ActivityAccordionProps {
  sessions: AgentSessionWithStatus[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function ActivityAccordion({
  sessions,
  selectedId,
  onSelect,
}: ActivityAccordionProps) {
  // Group sessions by status category
  const groupedSessions = useMemo(() => {
    const groups: Record<StatusGroup, AgentSessionWithStatus[]> = {
      review_required: [],
      running: [],
      error: [],
      completed: [],
    }

    for (const session of sessions) {
      for (const [groupKey, config] of Object.entries(STATUS_GROUPS)) {
        if (config.statuses.includes(session.derivedStatus)) {
          groups[groupKey as StatusGroup].push(session)
          break
        }
      }
    }

    // Sort sessions within each group by updated_at (most recent first)
    for (const groupKey of Object.keys(groups) as StatusGroup[]) {
      groups[groupKey].sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      )
    }

    return groups
  }, [sessions])

  // Calculate which groups have items and should be expanded by default
  const defaultExpandedGroups = useMemo(() => {
    return GROUP_ORDER.filter((group) => groupedSessions[group].length > 0)
  }, [groupedSessions])

  return (
    <ScrollArea className="h-full">
      <AccordionPrimitive.Root
        type="multiple"
        defaultValue={defaultExpandedGroups}
        className="w-full"
      >
        {GROUP_ORDER.map((groupKey) => {
          const config = STATUS_GROUPS[groupKey]
          const groupSessions = groupedSessions[groupKey]
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
                    groupKey === "review_required" &&
                      "data-[state=open]:bg-primary/[0.03] data-[state=open]:border-l-primary dark:data-[state=open]:bg-primary/[0.08]",
                    groupKey === "running" &&
                      "data-[state=open]:bg-muted/50 data-[state=open]:border-l-muted-foreground",
                    groupKey === "error" &&
                      "data-[state=open]:bg-red-600/[0.03] data-[state=open]:border-l-red-600 dark:data-[state=open]:bg-red-600/[0.08]",
                    groupKey === "completed" &&
                      "data-[state=open]:bg-green-600/[0.03] data-[state=open]:border-l-green-600 dark:data-[state=open]:bg-green-600/[0.08]"
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
                    <span className="text-sm font-medium">{config.label}</span>
                    <span className="text-sm text-muted-foreground">
                      {groupSessions.length}
                    </span>
                  </div>
                </AccordionPrimitive.Trigger>
              </AccordionPrimitive.Header>
              <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                <div className="ml-[18px]">
                  {groupSessions.map((session) => (
                    <ActivityItem
                      key={session.id}
                      session={session}
                      isSelected={selectedId === session.id}
                      onClick={() => onSelect(session.id)}
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
