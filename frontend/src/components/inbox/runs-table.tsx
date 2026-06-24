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
import {
  INBOX_GROUP_ORDER,
  type InboxGroupState,
  type InboxOrderBy,
} from "@/hooks/use-inbox"
import type { InboxSessionItem } from "@/lib/agents"
import { getDisplayName as getUserDisplayName } from "@/lib/auth"
import { cn } from "@/lib/utils"

// Only columns the API can order globally across pages are sortable. Title,
// source, and created_by have no server-side keyset equivalent, so sorting them
// would only reorder the rows already loaded in the browser — misleading once a
// group spans multiple pages — and they are rendered as plain labels instead.
type SortKey = Extract<InboxOrderBy, "created_at" | "updated_at">
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
// The title column keeps a min width (rather than minmax(0,...)) so it can't be
// crushed to nothing; combined with the fixed columns this gives the grid a
// natural minimum width that the surrounding GRID_SCROLL wrapper scrolls
// horizontally once the pane narrows the inset. Without this the title wraps
// vertically and the actions column slides offscreen, leaving Delete approval
// unreachable.
const GRID_COLS = "grid-cols-[minmax(12rem,1fr)_7rem_10rem_8rem_8rem]"
const GRID_COLS_WITH_ACTIONS =
  "grid-cols-[minmax(12rem,1fr)_7rem_10rem_8rem_8rem_2rem]"

// Applied to the header and the scroll body so both share one horizontal
// scroll context and stay column-aligned when the table overflows its inset.
const GRID_SCROLL = "min-w-[48rem]"

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
    // copilot (workspace chat) and approval (inbox continuation) are both
    // chat-shaped sessions; the default covers them and any future entity type.
    case "copilot":
    case "approval":
      return "chat"
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

interface SortableHeadProps {
  label: string
  sortKey: SortKey
  activeKey: SortKey | null
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

  // Expose sort state to assistive tech. These headers are grid cells (divs),
  // not table columnheaders, so aria-sort would be invalid on the button;
  // aria-pressed plus a descriptive label conveys the same state and intent.
  // aria-label replaces the visible text, so keep the column name in both
  // states or screen-reader users lose track of which column this sorts.
  let actionDescription = `Sort by ${label}`
  if (isActive) {
    const current = direction === "asc" ? "ascending" : "descending"
    const nextDirection = direction === "asc" ? "descending" : "ascending"
    actionDescription = `${label}, sorted ${current}. Activate to sort ${nextDirection}.`
  }

  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      aria-pressed={isActive}
      aria-label={actionDescription}
      className={cn(
        "flex items-center gap-1 text-xs font-medium transition-colors hover:text-foreground",
        isActive ? "text-foreground" : "text-muted-foreground",
        className
      )}
    >
      {label}
      <SortIcon className="size-3" aria-hidden="true" />
    </button>
  )
}

/** Non-sortable column header. Used for columns with no server-side order. */
function PlainHead({
  label,
  className,
}: {
  label: string
  className?: string
}) {
  return (
    <span
      className={cn("text-xs font-medium text-muted-foreground", className)}
    >
      {label}
    </span>
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
  orderBy: InboxOrderBy
  sort: SortDirection
  onSort: (key: SortKey) => void
}

/**
 * Agent runs grouped by status into accordion sections, with sortable
 * timestamp columns shared across groups. Sorting is applied server-side
 * (driven by `orderBy`/`sort`) so it orders every page of a group, not just the
 * rows already loaded. Each group is paginated independently and exposes a
 * "Show more" row when more items exist. Rows are clickable and open the run's
 * session detail; runs with pending approvals can be dismissed inline.
 */
export function RunsTable({
  groups,
  selectedId,
  deletingId,
  onSelect,
  onDelete,
  orderBy,
  sort,
  onSort,
}: RunsTableProps) {
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  // Track user-collapsed groups so newly appearing groups start expanded
  const [collapsedGroups, setCollapsedGroups] = useState<StatusGroup[]>([])

  const showActions = Boolean(onDelete)

  // The active sort column, narrowed to the columns this table renders as
  // sortable headers. When the inbox is ordered by `status`, no timestamp
  // header is highlighted.
  const activeKey: SortKey | null =
    orderBy === "created_at" || orderBy === "updated_at" ? orderBy : null

  // Only render groups that have items (loaded or unloaded); empty groups are hidden
  const visibleGroups = useMemo(() => {
    return INBOX_GROUP_ORDER.filter(
      (group) => groups[group].sessions.length > 0 || groups[group].hasMore
    )
  }, [groups])

  const expandedGroups = visibleGroups.filter(
    (group) => !collapsedGroups.includes(group)
  )

  const handleExpandedChange = (open: string[]) => {
    setCollapsedGroups(visibleGroups.filter((group) => !open.includes(group)))
  }

  return (
    // Horizontal scroll lives on the outer column so the header and the
    // vertically-scrolling body move together; the inner min-width (GRID_SCROLL)
    // gives them a shared overflow width once the inset is too narrow.
    <div className="flex h-full flex-col overflow-x-auto">
      {/* Global sortable column header shared by all status groups */}
      <div
        className={cn(
          "grid shrink-0 items-center gap-2 border-b py-1.5 pl-3 pr-3",
          GRID_SCROLL,
          showActions ? GRID_COLS_WITH_ACTIONS : GRID_COLS
        )}
      >
        <PlainHead label="Title" className="pl-10" />
        <PlainHead label="Source" />
        <PlainHead label="Created by" />
        <SortableHead
          label="Created"
          sortKey="created_at"
          activeKey={activeKey}
          direction={sort}
          onSort={onSort}
        />
        <SortableHead
          label="Updated"
          sortKey="updated_at"
          activeKey={activeKey}
          direction={sort}
          onSort={onSort}
        />
        {showActions && <span />}
      </div>

      <ScrollArea className={cn("min-h-0 flex-1", GRID_SCROLL)}>
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
            const groupState = groups[groupKey]
            const groupSessions = groupState.sessions
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
