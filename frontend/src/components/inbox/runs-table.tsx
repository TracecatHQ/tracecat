"use client"

import * as AccordionPrimitive from "@radix-ui/react-accordion"
import { format } from "date-fns"
import {
  AnvilIcon,
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  CirclePauseIcon,
  FlaskConicalIcon,
  LayersIcon,
  LoaderCircleIcon,
  MessageSquareIcon,
  Trash2Icon,
  WorkflowIcon,
  XCircleIcon,
} from "lucide-react"
import { useMemo, useState } from "react"
import type { InboxGroup } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import UserAvatar from "@/components/user-avatar"
import { INBOX_GROUP_ORDER, type InboxGroupState } from "@/hooks/use-inbox"
import type { InboxSessionItem } from "@/lib/agents"
import { getDisplayName as getUserDisplayName } from "@/lib/auth"
import { cn } from "@/lib/utils"

type SortKey = "title" | "source" | "created_by" | "created_at" | "updated_at"
type SortDirection = "asc" | "desc"

type SourceType = "workflow" | "case" | "chat" | "test" | "assistant"

interface SourceConfig {
  label: string
  icon: React.ComponentType<{ className?: string }>
}

const SOURCE_CONFIGS: Record<SourceType, SourceConfig> = {
  workflow: {
    label: "Workflow",
    icon: WorkflowIcon,
  },
  case: {
    label: "Case",
    icon: LayersIcon,
  },
  chat: {
    label: "Chat",
    icon: MessageSquareIcon,
  },
  test: {
    label: "Test",
    icon: FlaskConicalIcon,
  },
  assistant: {
    label: "Build",
    icon: AnvilIcon,
  },
}

type StatusGroup = InboxGroup

interface StatusGroupConfig {
  label: string
  icon: React.ComponentType<{ className?: string }>
  iconColor: string
}

const STATUS_GROUPS: Record<StatusGroup, StatusGroupConfig> = {
  review_required: {
    label: "Review required",
    icon: CirclePauseIcon,
    iconColor: "text-primary",
  },
  running: {
    label: "In progress",
    icon: LoaderCircleIcon,
    iconColor: "text-muted-foreground",
  },
  error: {
    label: "Error",
    icon: XCircleIcon,
    iconColor: "text-red-600",
  },
  completed: {
    label: "Completed",
    icon: CheckCircle2Icon,
    iconColor: "text-green-600",
  },
}

// Shared column template so the global header and group rows stay aligned.
const GRID_COLS = "grid-cols-[minmax(0,1fr)_7rem_10rem_8rem_8rem]"
const GRID_COLS_WITH_ACTIONS =
  "grid-cols-[minmax(0,1fr)_7rem_10rem_8rem_8rem_2rem]"

function getSourceType(entityType: string): SourceType {
  switch (entityType) {
    case "workflow":
    case "external_channel":
      return "workflow"
    case "case":
      return "case"
    case "agent_preset":
      return "test"
    case "agent_preset_builder":
      return "assistant"
    default:
      return "chat"
  }
}

function getDisplayName(session: InboxSessionItem): string {
  return (
    session.parent_workflow?.alias ||
    session.parent_workflow?.title ||
    session.title
  )
}

function getCreatorName(session: InboxSessionItem): string {
  if (!session.created_by) {
    return ""
  }
  return getUserDisplayName(session.created_by)
}

const DEFAULT_SORT_DIRECTIONS: Record<SortKey, SortDirection> = {
  title: "asc",
  source: "asc",
  created_by: "asc",
  created_at: "desc",
  updated_at: "desc",
}

function compareSessions(
  a: InboxSessionItem,
  b: InboxSessionItem,
  sortKey: SortKey
): number {
  switch (sortKey) {
    case "title":
      return getDisplayName(a).localeCompare(getDisplayName(b))
    case "source":
      return getSourceType(a.entity_type).localeCompare(
        getSourceType(b.entity_type)
      )
    case "created_by":
      return getCreatorName(a).localeCompare(getCreatorName(b))
    case "created_at":
      return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    case "updated_at":
      return new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime()
  }
}

interface SortableHeadProps {
  label: string
  sortKey: SortKey
  activeKey: SortKey
  direction: SortDirection
  onSort: (key: SortKey) => void
  className?: string
}

function SortableHead({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
  className,
}: SortableHeadProps) {
  const isActive = activeKey === sortKey
  let SortIcon = ArrowUpDownIcon
  if (isActive) {
    SortIcon = direction === "asc" ? ArrowUpIcon : ArrowDownIcon
  }

  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className={cn(
        "flex items-center gap-1 text-xs font-medium transition-colors hover:text-foreground",
        isActive ? "text-foreground" : "text-muted-foreground",
        className
      )}
    >
      {label}
      <SortIcon className="size-3" />
    </button>
  )
}

function formatTimestamp(date: Date): string {
  const now = new Date()
  if (date.getFullYear() === now.getFullYear()) {
    return format(date, "MMM d, HH:mm")
  }
  return format(date, "MMM d, yyyy")
}

function TimestampCell({ value, label }: { value: string; label: string }) {
  const date = new Date(value)
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-xs text-muted-foreground">
            {formatTimestamp(date)}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          {label}: {date.toLocaleString()}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

interface RunRowProps {
  session: InboxSessionItem
  isSelected: boolean
  isDeleting: boolean
  showActions: boolean
  onSelect: (id: string) => void
  onRequestDelete?: (id: string) => void
}

function RunRow({
  session,
  isSelected,
  isDeleting,
  showActions,
  onSelect,
  onRequestDelete,
}: RunRowProps) {
  const sourceConfig = SOURCE_CONFIGS[getSourceType(session.entity_type)]
  const SourceIcon = sourceConfig.icon

  return (
    <div
      onClick={() => onSelect(session.id)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) {
          return
        }
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onSelect(session.id)
        }
      }}
      role="button"
      tabIndex={0}
      className={cn(
        "group/row grid cursor-pointer items-center gap-2 border-b border-border/50 py-2 pl-3 pr-3 transition-colors",
        showActions ? GRID_COLS_WITH_ACTIONS : GRID_COLS,
        "hover:bg-muted/50",
        isSelected && "bg-muted hover:bg-muted"
      )}
    >
      <span className="block truncate pl-10 text-xs">
        {getDisplayName(session)}
      </span>
      <Badge
        variant="secondary"
        className="h-5 w-fit gap-1 px-1.5 py-0 text-[10px] font-normal"
      >
        <SourceIcon className="size-3" />
        {sourceConfig.label}
      </Badge>
      <div className="flex min-w-0 items-center gap-1.5">
        {session.created_by ? (
          <>
            <UserAvatar
              email={session.created_by.email}
              firstName={session.created_by.first_name}
              className="size-5 shrink-0"
              fallbackClassName="text-[9px]"
            />
            <span className="truncate text-xs text-muted-foreground">
              {getUserDisplayName(session.created_by)}
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </div>
      <TimestampCell value={session.created_at} label="Created" />
      <TimestampCell value={session.updated_at} label="Updated" />
      {showActions && (
        <div className="flex justify-end">
          {onRequestDelete && session.pendingApprovalCount > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onRequestDelete(session.id)
              }}
              disabled={isDeleting}
              className={cn(
                "flex size-6 items-center justify-center rounded text-muted-foreground transition-colors",
                "opacity-0 group-hover/row:opacity-100",
                "hover:bg-destructive/10 hover:text-destructive",
                isDeleting && "cursor-not-allowed opacity-50"
              )}
              aria-label="Delete approval"
            >
              <Trash2Icon className="size-3.5" />
            </button>
          )}
        </div>
      )}
    </div>
  )
}

interface RunsTableProps {
  groups: Record<InboxGroup, InboxGroupState>
  selectedId: string | null
  deletingId: string | null
  onSelect: (id: string) => void
  onDelete?: (id: string) => void
}

/**
 * Agent runs grouped by status into accordion sections, with sortable
 * columns shared across groups. Each group is paginated independently on
 * the server and exposes a "Show more" row when more items exist. Rows are
 * clickable and open the run's session detail; runs with pending approvals
 * can be dismissed inline.
 */
export function RunsTable({
  groups,
  selectedId,
  deletingId,
  onSelect,
  onDelete,
}: RunsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("updated_at")
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc")
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  // Track user-collapsed groups so newly appearing groups start expanded
  const [collapsedGroups, setCollapsedGroups] = useState<StatusGroup[]>([])

  const showActions = Boolean(onDelete)

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDirection(DEFAULT_SORT_DIRECTIONS[key])
    }
  }

  // Sort each server-provided group's loaded sessions by the active column
  const groupedSessions = useMemo(() => {
    const factor = sortDirection === "asc" ? 1 : -1
    const sorted = {} as Record<StatusGroup, InboxSessionItem[]>
    for (const groupKey of INBOX_GROUP_ORDER) {
      sorted[groupKey] = [...groups[groupKey].sessions].sort((a, b) => {
        const primary = compareSessions(a, b, sortKey) * factor
        if (primary !== 0) {
          return primary
        }
        // Tiebreak on most recently updated first
        return (
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        )
      })
    }
    return sorted
  }, [groups, sortKey, sortDirection])

  // Only render groups that have items (loaded or unloaded); empty groups are hidden
  const visibleGroups = useMemo(() => {
    return INBOX_GROUP_ORDER.filter(
      (group) => groupedSessions[group].length > 0 || groups[group].hasMore
    )
  }, [groupedSessions, groups])

  const expandedGroups = visibleGroups.filter(
    (group) => !collapsedGroups.includes(group)
  )

  const handleExpandedChange = (open: string[]) => {
    setCollapsedGroups(visibleGroups.filter((group) => !open.includes(group)))
  }

  return (
    <div className="flex h-full flex-col">
      {/* Global sortable column header shared by all status groups */}
      <div
        className={cn(
          "grid shrink-0 items-center gap-2 border-b py-1.5 pl-3 pr-3",
          showActions ? GRID_COLS_WITH_ACTIONS : GRID_COLS
        )}
      >
        <SortableHead
          label="Title"
          sortKey="title"
          activeKey={sortKey}
          direction={sortDirection}
          onSort={handleSort}
          className="pl-10"
        />
        <SortableHead
          label="Source"
          sortKey="source"
          activeKey={sortKey}
          direction={sortDirection}
          onSort={handleSort}
        />
        <SortableHead
          label="Created by"
          sortKey="created_by"
          activeKey={sortKey}
          direction={sortDirection}
          onSort={handleSort}
        />
        <SortableHead
          label="Created"
          sortKey="created_at"
          activeKey={sortKey}
          direction={sortDirection}
          onSort={handleSort}
        />
        <SortableHead
          label="Updated"
          sortKey="updated_at"
          activeKey={sortKey}
          direction={sortDirection}
          onSort={handleSort}
        />
        {showActions && <span />}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        {visibleGroups.length === 0 && (
          <div className="flex h-32 items-center justify-center">
            <span className="text-sm text-muted-foreground">No agent runs</span>
          </div>
        )}
        <AccordionPrimitive.Root
          type="multiple"
          value={expandedGroups}
          onValueChange={handleExpandedChange}
          className="w-full"
        >
          {visibleGroups.map((groupKey) => {
            const config = STATUS_GROUPS[groupKey]
            const groupSessions = groupedSessions[groupKey]
            const groupState = groups[groupKey]
            const StatusIcon = config.icon

            return (
              <AccordionPrimitive.Item
                key={groupKey}
                value={groupKey}
                className="group/accordion border-b border-border/50"
                data-status={groupKey}
              >
                <AccordionPrimitive.Header className="sticky top-0 z-10 flex bg-background">
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
                        className={cn(
                          "size-4 shrink-0",
                          config.iconColor,
                          groupKey === "running" && "animate-spin"
                        )}
                      />
                      <span className="text-xs font-medium">
                        {config.label}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {groupSessions.length}
                        {groupState.hasMore ? "+" : ""}
                      </span>
                    </div>
                  </AccordionPrimitive.Trigger>
                </AccordionPrimitive.Header>
                <AccordionPrimitive.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
                  {groupSessions.map((session) => (
                    <RunRow
                      key={session.id}
                      session={session}
                      isSelected={selectedId === session.id}
                      isDeleting={deletingId === session.id}
                      showActions={showActions}
                      onSelect={onSelect}
                      onRequestDelete={
                        onDelete && groupKey === "review_required"
                          ? setConfirmDeleteId
                          : undefined
                      }
                    />
                  ))}
                  {groupState.hasMore && (
                    <button
                      type="button"
                      onClick={groupState.loadMore}
                      disabled={groupState.isLoadingMore}
                      className={cn(
                        "flex w-full items-center justify-center gap-1.5 border-b border-border/50 py-2 text-xs text-muted-foreground transition-colors",
                        "hover:bg-muted/50 hover:text-foreground",
                        groupState.isLoadingMore &&
                          "cursor-not-allowed opacity-50"
                      )}
                    >
                      {groupState.isLoadingMore ? (
                        <>
                          <LoaderCircleIcon className="size-3.5 animate-spin" />
                          Loading…
                        </>
                      ) : (
                        "Show more"
                      )}
                    </button>
                  )}
                </AccordionPrimitive.Content>
              </AccordionPrimitive.Item>
            )
          })}
        </AccordionPrimitive.Root>
      </ScrollArea>

      <AlertDialog
        open={confirmDeleteId !== null}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmDeleteId(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete approval</AlertDialogTitle>
            <AlertDialogDescription>
              This will deny all pending tool calls and remove this approval
              from the inbox.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (confirmDeleteId) {
                  onDelete?.(confirmDeleteId)
                }
                setConfirmDeleteId(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
