"use client"

import { format } from "date-fns"
import {
  AtSignIcon,
  CalendarClockIcon,
  CircleCheck,
  CircleDot,
  Clock3,
  FlagTriangleRight,
  FolderIcon,
  HistoryIcon,
  WebhookIcon,
  WorkflowIcon,
} from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type {
  CaseEventType,
  FolderDirectoryItem,
  TagRead,
  WorkflowDirectoryItem,
  WorkflowReadMinimal,
} from "@/client"
import { DeleteWorkflowAlertDialog } from "@/components/dashboard/delete-workflow-dialog"
import { FolderDeleteAlertDialog } from "@/components/dashboard/folder-delete-dialog"
import { FolderMoveDialog } from "@/components/dashboard/folder-move-dialog"
import { FolderRenameDialog } from "@/components/dashboard/folder-rename-dialog"
import {
  FolderActions,
  WorkflowActions,
} from "@/components/dashboard/table-actions"
import { ActiveDialog } from "@/components/dashboard/table-common"
import { WorkflowMoveDialog } from "@/components/dashboard/workflow-move-dialog"
import {
  DEFAULT_WORKFLOW_SORT,
  type WorkflowCaseTriggerFilterValue,
  type WorkflowScheduleFilterValue,
  WorkflowsHeader,
  type WorkflowsSortValue,
  type WorkflowsViewMode,
  type WorkflowWebhookFilterValue,
} from "@/components/dashboard/workflows-header"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useWorkflowsPagination } from "@/hooks/pagination/use-workflows-pagination"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  type DirectoryItem,
  useGetDirectoryItems,
  useWorkflowTags,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const DEFAULT_LIMIT = 20
const ROW_NAME_COLUMN_CLASS = "min-w-0 w-[340px] shrink-0 truncate text-xs"

function parseWorkflowsViewMode(value: string | null): WorkflowsViewMode {
  return value === "list" ? "list" : "folders"
}

function normalizeFolderPath(rawPath: string | null): string {
  if (!rawPath || rawPath === "/") {
    return "/"
  }

  const withLeadingSlash = rawPath.startsWith("/") ? rawPath : `/${rawPath}`
  return withLeadingSlash.endsWith("/") && withLeadingSlash !== "/"
    ? withLeadingSlash.slice(0, -1)
    : withLeadingSlash
}

function getRelativeDateLabel(dateValue: string): string {
  const timestamp = new Date(dateValue).getTime()
  if (Number.isNaN(timestamp)) {
    return "0m"
  }

  const diffMs = Math.max(0, Date.now() - timestamp)
  const minuteMs = 60_000
  const hourMs = 60 * minuteMs
  const dayMs = 24 * hourMs
  const monthMs = 30 * dayMs
  const yearMs = 365 * dayMs

  if (diffMs < hourMs) {
    return `${Math.max(1, Math.floor(diffMs / minuteMs))}m`
  }
  if (diffMs < dayMs) {
    return `${Math.max(1, Math.floor(diffMs / hourMs))}hr`
  }
  if (diffMs < monthMs) {
    return `${Math.max(1, Math.floor(diffMs / dayMs))}d`
  }
  if (diffMs < yearMs) {
    return `${Math.max(1, Math.floor(diffMs / monthMs))}mo`
  }
  return `${Math.max(1, Math.floor(diffMs / yearMs))}y`
}

function WorkflowTagPills({ tags }: { tags?: TagRead[] | null }) {
  if (!tags || tags.length === 0) {
    return null
  }

  return (
    <div className="flex min-w-0 items-center gap-1">
      {tags.slice(0, 3).map((tag) => (
        <span
          key={tag.id}
          className={cn(
            "inline-flex h-5 max-w-[110px] items-center truncate rounded-full px-2 text-[10px] font-medium",
            !tag.color && "bg-muted text-muted-foreground"
          )}
          style={
            tag.color
              ? {
                  backgroundColor: `${tag.color}20`,
                  color: tag.color,
                }
              : undefined
          }
        >
          {tag.name}
        </span>
      ))}
      {tags.length > 3 && (
        <span className="text-[10px] text-muted-foreground">
          +{tags.length - 3}
        </span>
      )}
    </div>
  )
}

function WorkflowMetadataBadges({ item }: { item: WorkflowDirectoryItem }) {
  const lastPublished = item.latest_definition?.created_at ?? null
  const version = item.latest_definition?.version ?? null

  return (
    <div className="flex shrink-0 items-center gap-1">
      {item.alias && (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <AtSignIcon className="mr-1 size-3" />
          {item.alias}
        </Badge>
      )}

      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="secondary"
            className="h-5 cursor-default px-2 text-[10px] font-normal"
          >
            <Clock3 className="mr-1 size-3" />
            {getRelativeDateLabel(item.updated_at)}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {format(new Date(item.updated_at), "PPpp")}
        </TooltipContent>
      </Tooltip>

      {lastPublished ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              className="h-5 cursor-default px-2 text-[10px] font-normal"
            >
              <CircleCheck className="mr-1 size-3" />
              {getRelativeDateLabel(lastPublished)}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            {format(new Date(lastPublished), "PPpp")}
          </TooltipContent>
        </Tooltip>
      ) : (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <CircleDot className="mr-1 size-3" />
          Unpublished
        </Badge>
      )}

      {version !== null && (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <HistoryIcon className="mr-1 size-3" />v{version}
        </Badge>
      )}
    </div>
  )
}

type WorkflowTriggerSummary = {
  schedule_count_online: number
  schedule_cron?: string | null
  schedule_natural?: string | null
  webhook_active: boolean
  case_trigger_events?: CaseEventType[] | null
}

type WorkflowDirectoryItemWithTriggerSummary = WorkflowDirectoryItem & {
  trigger_summary?: WorkflowTriggerSummary | null
}

function getWorkflowTriggerSummary(
  item: WorkflowDirectoryItem
): WorkflowTriggerSummary | null {
  return (
    (item as WorkflowDirectoryItemWithTriggerSummary).trigger_summary ?? null
  )
}

const MINUTE_MS = 60_000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS
const WEEK_MS = 7 * DAY_MS
const TWO_WEEKS_MS = 2 * WEEK_MS
const SCHEDULE_TOKEN_PATTERN = /^(\d+)([smhd])$/
const DAILY_SCHEDULE_PATTERN = /^Every 1d \d{2}:\d{2} UTC$/
const WEEKLY_SCHEDULE_PATTERN =
  /^Every (Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday) at \d{2}:\d{2} UTC$/
const HOURLY_OFFSET_SCHEDULE_PATTERN = /^Every 1h :\d{2}$/

function parseNaturalScheduleIntervalMs(
  scheduleNatural: string
): number | null {
  if (HOURLY_OFFSET_SCHEDULE_PATTERN.test(scheduleNatural)) {
    return HOUR_MS
  }
  if (DAILY_SCHEDULE_PATTERN.test(scheduleNatural)) {
    return DAY_MS
  }
  if (WEEKLY_SCHEDULE_PATTERN.test(scheduleNatural)) {
    return WEEK_MS
  }
  if (!scheduleNatural.startsWith("Every ")) {
    return null
  }

  const tokens = scheduleNatural
    .slice("Every ".length)
    .trim()
    .split(/\s+/)
    .filter(Boolean)

  let intervalMs = 0
  for (const token of tokens) {
    const match = token.match(SCHEDULE_TOKEN_PATTERN)
    if (!match) {
      return null
    }

    const amount = Number(match[1])
    const unit = match[2]
    if (!Number.isFinite(amount) || amount <= 0) {
      return null
    }

    if (unit === "s") {
      intervalMs += amount * 1000
      continue
    }
    if (unit === "m") {
      intervalMs += amount * MINUTE_MS
      continue
    }
    if (unit === "h") {
      intervalMs += amount * HOUR_MS
      continue
    }
    intervalMs += amount * DAY_MS
  }

  return intervalMs > 0 ? intervalMs : null
}

function parseCronScheduleIntervalMs(scheduleCron: string): number | null {
  const rawParts = scheduleCron.trim().split(/\s+/)
  if (rawParts.length !== 5 && rawParts.length !== 6) {
    return null
  }

  const parts = rawParts.length === 6 ? rawParts.slice(1) : rawParts
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts
  const isInteger = (value: string) => /^\d+$/.test(value)
  const minuteStepMatch = minute.match(/^\*\/(\d+)$/)
  const hourStepMatch = hour.match(/^\*\/(\d+)$/)

  if (dayOfMonth !== "*" || month !== "*") {
    return WEEK_MS
  }

  if (
    isInteger(minute) &&
    isInteger(hour) &&
    dayOfWeek !== "*" &&
    dayOfWeek !== "?"
  ) {
    return WEEK_MS
  }

  if (
    (minute === "*" || minute === "*/1") &&
    hour === "*" &&
    dayOfWeek === "*"
  ) {
    return MINUTE_MS
  }

  if (minuteStepMatch && hour === "*" && dayOfWeek === "*") {
    const interval = Number(minuteStepMatch[1])
    return interval > 0 ? interval * MINUTE_MS : null
  }

  if (isInteger(minute) && hour === "*" && dayOfWeek === "*") {
    return HOUR_MS
  }

  if (isInteger(minute) && hourStepMatch && dayOfWeek === "*") {
    const interval = Number(hourStepMatch[1])
    return interval > 0 ? interval * HOUR_MS : null
  }

  if (isInteger(minute) && isInteger(hour) && dayOfWeek === "*") {
    return DAY_MS
  }

  return null
}

function getScheduleIntervalMs(
  triggerSummary: WorkflowTriggerSummary | null
): number | null {
  if (!triggerSummary) {
    return null
  }

  if (triggerSummary.schedule_natural) {
    const fromNatural = parseNaturalScheduleIntervalMs(
      triggerSummary.schedule_natural
    )
    if (fromNatural !== null) {
      return fromNatural
    }
  }

  if (triggerSummary.schedule_cron) {
    return parseCronScheduleIntervalMs(triggerSummary.schedule_cron)
  }

  return null
}

function matchesScheduleFilter(
  triggerSummary: WorkflowTriggerSummary | null,
  scheduleFilter: WorkflowScheduleFilterValue
): boolean {
  if (scheduleFilter === "all") {
    return true
  }

  const hasActiveSchedule = (triggerSummary?.schedule_count_online ?? 0) > 0
  if (scheduleFilter === "never") {
    return !hasActiveSchedule
  }
  if (!hasActiveSchedule) {
    return false
  }

  const scheduleIntervalMs = getScheduleIntervalMs(triggerSummary)
  if (scheduleIntervalMs === null) {
    return scheduleFilter === "more_than_week"
  }

  if (scheduleFilter === "within_minutes") {
    return scheduleIntervalMs < HOUR_MS
  }
  if (scheduleFilter === "within_hours") {
    return scheduleIntervalMs >= HOUR_MS && scheduleIntervalMs < DAY_MS
  }
  if (scheduleFilter === "within_days") {
    return scheduleIntervalMs >= DAY_MS && scheduleIntervalMs < WEEK_MS
  }
  if (scheduleFilter === "within_week") {
    return scheduleIntervalMs >= WEEK_MS && scheduleIntervalMs < TWO_WEEKS_MS
  }
  return scheduleIntervalMs >= TWO_WEEKS_MS
}

function WorkflowTriggerBadges({ item }: { item: WorkflowDirectoryItem }) {
  const BADGE_GAP_PX = 4
  const triggerSummary = getWorkflowTriggerSummary(item)

  if (!triggerSummary) {
    return null
  }

  const onlineScheduleCount = triggerSummary.schedule_count_online ?? 0
  const caseTriggerEvents = triggerSummary.case_trigger_events ?? []
  const hasSchedule = onlineScheduleCount > 0
  const hasWebhook = Boolean(triggerSummary.webhook_active)
  const hasCaseTriggers = caseTriggerEvents.length > 0

  if (!hasSchedule && !hasWebhook && !hasCaseTriggers) {
    return null
  }

  const scheduleLabel =
    triggerSummary.schedule_natural ||
    triggerSummary.schedule_cron ||
    `${onlineScheduleCount} schedules`

  const caseEventsLabel = caseTriggerEvents.slice(0, 2).join(", ")
  const caseEventsOverflow =
    caseTriggerEvents.length > 2 ? ` +${caseTriggerEvents.length - 2}` : ""
  const caseEventsTooltip = caseTriggerEvents.join(", ")

  const badges = useMemo(
    () =>
      [
        hasSchedule
          ? {
              id: "schedule",
              node: (
                <Tooltip key="schedule">
                  <TooltipTrigger asChild>
                    <Badge
                      variant="secondary"
                      className="h-5 cursor-default px-2 text-[10px] font-normal"
                    >
                      <CalendarClockIcon className="mr-1 size-3" />
                      <span className="max-w-[220px] truncate">
                        {scheduleLabel}
                      </span>
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    <div className="space-y-1 text-xs">
                      {triggerSummary.schedule_cron ? (
                        <p className="font-mono">
                          {triggerSummary.schedule_cron}
                        </p>
                      ) : null}
                      {triggerSummary.schedule_natural ? (
                        <p>{triggerSummary.schedule_natural}</p>
                      ) : null}
                    </div>
                  </TooltipContent>
                </Tooltip>
              ),
            }
          : null,
        hasWebhook
          ? {
              id: "webhook",
              node: (
                <Badge
                  key="webhook"
                  variant="secondary"
                  className="h-5 px-2 text-[10px] font-normal"
                >
                  <WebhookIcon className="mr-1 size-3" />
                  Webhook
                </Badge>
              ),
            }
          : null,
        hasCaseTriggers
          ? {
              id: "case-triggers",
              node: (
                <Tooltip key="case-triggers">
                  <TooltipTrigger asChild>
                    <Badge
                      variant="secondary"
                      className="h-5 cursor-default px-2 text-[10px] font-normal"
                    >
                      <FlagTriangleRight className="mr-1 size-3" />
                      <span className="max-w-[240px] truncate">
                        {caseEventsLabel}
                        {caseEventsOverflow}
                      </span>
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>{caseEventsTooltip}</TooltipContent>
                </Tooltip>
              ),
            }
          : null,
      ].filter((badge): badge is { id: string; node: JSX.Element } =>
        Boolean(badge)
      ),
    [
      caseEventsLabel,
      caseEventsOverflow,
      caseEventsTooltip,
      hasCaseTriggers,
      hasSchedule,
      hasWebhook,
      scheduleLabel,
      triggerSummary.schedule_cron,
      triggerSummary.schedule_natural,
    ]
  )

  const badgeLayoutKey = useMemo(
    () =>
      `${scheduleLabel}|${triggerSummary.schedule_cron ?? ""}|${triggerSummary.schedule_natural ?? ""}|${hasWebhook}|${caseEventsTooltip}`,
    [
      caseEventsTooltip,
      hasWebhook,
      scheduleLabel,
      triggerSummary.schedule_cron,
      triggerSummary.schedule_natural,
    ]
  )

  const viewportRef = useRef<HTMLDivElement | null>(null)
  const measurementRef = useRef<HTMLDivElement | null>(null)
  const [firstVisibleBadgeIndex, setFirstVisibleBadgeIndex] = useState(0)

  useLayoutEffect(() => {
    const updateVisibleBadges = () => {
      const viewport = viewportRef.current
      const measurement = measurementRef.current
      if (!viewport || !measurement) {
        setFirstVisibleBadgeIndex(0)
        return
      }

      if (badges.length <= 1) {
        setFirstVisibleBadgeIndex(0)
        return
      }

      const availableWidth = viewport.clientWidth
      const badgeWidths = badges.map(
        (badge) =>
          measurement.querySelector<HTMLElement>(
            `[data-badge-id="${badge.id}"]`
          )?.offsetWidth ?? 0
      )

      if (availableWidth <= 0 || badgeWidths.some((width) => width <= 0)) {
        setFirstVisibleBadgeIndex(0)
        return
      }

      let usedWidth = 0
      let firstVisibleIndex = badges.length

      for (let index = badges.length - 1; index >= 0; index -= 1) {
        const gapBeforeBadge = usedWidth > 0 ? BADGE_GAP_PX : 0
        const projectedWidth = usedWidth + gapBeforeBadge + badgeWidths[index]

        if (projectedWidth <= availableWidth) {
          usedWidth += gapBeforeBadge + badgeWidths[index]
          firstVisibleIndex = index
          continue
        }
        break
      }

      setFirstVisibleBadgeIndex(firstVisibleIndex)
    }

    updateVisibleBadges()

    const resizeObserver = new ResizeObserver(() => {
      window.requestAnimationFrame(updateVisibleBadges)
    })
    if (viewportRef.current) {
      resizeObserver.observe(viewportRef.current)
    }
    if (measurementRef.current) {
      resizeObserver.observe(measurementRef.current)
    }

    return () => {
      resizeObserver.disconnect()
    }
  }, [BADGE_GAP_PX, badgeLayoutKey, badges])

  const visibleBadges = badges.slice(firstVisibleBadgeIndex)

  return (
    <div className="relative ml-auto flex min-w-0 max-w-[48%] shrink items-center justify-end">
      <div ref={viewportRef} className="min-w-0 max-w-full">
        <div className="flex items-center justify-end gap-1 overflow-hidden">
          {visibleBadges.map((badge) => (
            <div key={badge.id} className="shrink-0">
              {badge.node}
            </div>
          ))}
        </div>
      </div>

      <div
        ref={measurementRef}
        className="pointer-events-none absolute -z-10 h-0 overflow-hidden opacity-0"
        aria-hidden
      >
        <div className="flex w-max items-center gap-1">
          {badges.map((badge) => (
            <div key={badge.id} data-badge-id={badge.id}>
              {badge.node}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function FolderMetadataBadges({ item }: { item: FolderDirectoryItem }) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="secondary"
            className="h-5 cursor-default px-2 text-[10px] font-normal"
          >
            <Clock3 className="mr-1 size-3" />
            {getRelativeDateLabel(item.updated_at)}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {format(new Date(item.updated_at), "PPpp")}
        </TooltipContent>
      </Tooltip>

      <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
        <WorkflowIcon className="mr-1 size-3" />
        {item.num_items} workflows
      </Badge>
    </div>
  )
}

function getItemName(item: DirectoryItem): string {
  return item.type === "workflow" ? item.title : item.name
}

function getWorkflowVersionForSort(item: DirectoryItem): number | null {
  if (item.type !== "workflow") {
    return null
  }
  return item.latest_definition?.version ?? item.version ?? null
}

function compareItemsBySort(
  a: DirectoryItem,
  b: DirectoryItem,
  sortBy: WorkflowsSortValue
): number {
  // Keep folders pinned above workflows regardless of sort field/direction.
  if (a.type !== b.type) {
    return a.type === "folder" ? -1 : 1
  }

  const direction = sortBy.direction === "asc" ? 1 : -1

  if (sortBy.field === "name") {
    return (
      getItemName(a).localeCompare(getItemName(b), undefined, {
        numeric: true,
        sensitivity: "base",
      }) * direction
    )
  }

  if (sortBy.field === "version") {
    const aVersion = getWorkflowVersionForSort(a)
    const bVersion = getWorkflowVersionForSort(b)

    if (aVersion !== bVersion) {
      if (aVersion === null && bVersion === null) {
        return 0
      }
      if (aVersion === null) {
        return 1
      }
      if (bVersion === null) {
        return -1
      }
      return (aVersion - bVersion) * direction
    }

    return (
      getItemName(a).localeCompare(getItemName(b), undefined, {
        numeric: true,
        sensitivity: "base",
      }) * direction
    )
  }

  const aTimestamp = Date.parse(a[sortBy.field])
  const bTimestamp = Date.parse(b[sortBy.field])

  if (aTimestamp !== bTimestamp) {
    if (Number.isNaN(aTimestamp) && Number.isNaN(bTimestamp)) {
      return 0
    }
    if (Number.isNaN(aTimestamp)) {
      return 1
    }
    if (Number.isNaN(bTimestamp)) {
      return -1
    }
    return (aTimestamp - bTimestamp) * direction
  }

  return (
    getItemName(a).localeCompare(getItemName(b), undefined, {
      numeric: true,
      sensitivity: "base",
    }) * direction
  )
}

function WorkflowsListRow({
  item,
  onOpenWorkflow,
  onOpenFolder,
  setSelectedWorkflow,
  setSelectedFolder,
  setActiveDialog,
  availableTags,
}: {
  item: DirectoryItem
  onOpenWorkflow: (workflowId: string) => void
  onOpenFolder: (path: string) => void
  setSelectedWorkflow: (workflow: WorkflowReadMinimal | null) => void
  setSelectedFolder: (folder: FolderDirectoryItem | null) => void
  setActiveDialog: (activeDialog: ActiveDialog | null) => void
  availableTags?: TagRead[]
}) {
  const [isContextMenuOpen, setIsContextMenuOpen] = useState(false)

  if (item.type === "folder") {
    return (
      <ContextMenu onOpenChange={setIsContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            className={cn(
              "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
              isContextMenuOpen && "bg-muted/70"
            )}
          >
            <button
              type="button"
              onClick={() => onOpenFolder(item.path)}
              className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
            >
              <FolderIcon className="size-4 shrink-0 text-black" />
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <span className={ROW_NAME_COLUMN_CLASS}>{item.name}</span>
                <FolderMetadataBadges item={item} />
              </div>
            </button>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <FolderActions
            item={item}
            setActiveDialog={setActiveDialog}
            setSelectedFolder={setSelectedFolder}
          />
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  return (
    <ContextMenu onOpenChange={setIsContextMenuOpen}>
      <ContextMenuTrigger asChild>
        <div
          className={cn(
            "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
            isContextMenuOpen && "bg-muted/70"
          )}
        >
          <button
            type="button"
            onClick={() => onOpenWorkflow(item.id)}
            className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
          >
            <WorkflowIcon className="size-4 shrink-0 text-primary" />
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span className={ROW_NAME_COLUMN_CLASS}>{item.title}</span>
              <div className="flex min-w-0 flex-1 items-center justify-start gap-2 overflow-hidden">
                <WorkflowMetadataBadges item={item} />
                <WorkflowTagPills tags={item.tags} />
              </div>
              <WorkflowTriggerBadges item={item} />
            </div>
          </button>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        {/* Reuse the existing workflow action set, now rendered in right-click menu */}
        <WorkflowActions
          item={item}
          availableTags={availableTags}
          showMoveToFolder
          setSelectedWorkflow={setSelectedWorkflow}
          setActiveDialog={setActiveDialog}
        />
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function WorkflowsDashboard() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")

  const view = parseWorkflowsViewMode(searchParams?.get("view"))
  const currentPath = normalizeFolderPath(searchParams?.get("path"))

  const [searchQuery, setSearchQuery] = useState("")
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [webhookFilter, setWebhookFilter] =
    useState<WorkflowWebhookFilterValue>("all")
  const [scheduleFilter, setScheduleFilter] =
    useState<WorkflowScheduleFilterValue>("all")
  const [caseTriggerFilter, setCaseTriggerFilter] =
    useState<WorkflowCaseTriggerFilterValue>([])
  const [sortBy, setSortBy] = useState<WorkflowsSortValue>(
    DEFAULT_WORKFLOW_SORT
  )
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [folderPage, setFolderPage] = useState(0)

  const [activeDialog, setActiveDialog] = useState<ActiveDialog | null>(null)
  const [selectedWorkflow, setSelectedWorkflow] =
    useState<WorkflowReadMinimal | null>(null)
  const [selectedFolder, setSelectedFolder] =
    useState<FolderDirectoryItem | null>(null)

  const { tags } = useWorkflowTags(workspaceId)

  const tagNameByRef = useMemo(() => {
    const map = new Map<string, string>()
    for (const tag of tags ?? []) {
      map.set(tag.ref, tag.name)
    }
    return map
  }, [tags])

  const workflowTagNames = useMemo(
    () =>
      tagFilter
        .map((ref) => tagNameByRef.get(ref))
        .filter((name): name is string => Boolean(name)),
    [tagFilter, tagNameByRef]
  )

  const workflowPagination = useWorkflowsPagination({
    workspaceId,
    limit,
    tags: workflowTagNames,
    enabled: view === "list",
  })

  const { directoryItems, directoryItemsError, directoryItemsIsLoading } =
    useGetDirectoryItems(currentPath, workspaceId, {
      enabled: view === "folders",
    })

  const baseRoute = `/workspaces/${workspaceId}/workflows`

  const buildRoute = (params: URLSearchParams): string => {
    const query = params.toString()
    if (!query) {
      return baseRoute
    }
    return `${baseRoute}?${query}`
  }

  const handleViewChange = (nextView: WorkflowsViewMode) => {
    const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
    nextParams.set("view", nextView)

    if (nextView === "list") {
      nextParams.delete("path")
    } else if (!nextParams.has("path")) {
      nextParams.set("path", "/")
    }

    router.replace(buildRoute(nextParams))
  }

  const handleOpenFolder = (path: string) => {
    const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
    nextParams.set("view", "folders")
    nextParams.set("path", normalizeFolderPath(path))
    router.push(buildRoute(nextParams))
  }

  const tagFilterSet = useMemo(() => new Set(tagFilter), [tagFilter])
  const caseTriggerFilterSet = useMemo(
    () => new Set(caseTriggerFilter),
    [caseTriggerFilter]
  )
  const hasTriggerFiltersActive =
    webhookFilter !== "all" ||
    scheduleFilter !== "all" ||
    (caseAddonsEnabled && caseTriggerFilterSet.size > 0)
  const normalizedSearch = useMemo(
    () => searchQuery.trim().toLowerCase(),
    [searchQuery]
  )

  const matchesFilters = useCallback(
    (item: DirectoryItem): boolean => {
      if (normalizedSearch) {
        const searchable =
          item.type === "workflow"
            ? `${item.title} ${item.alias ?? ""} ${item.id}`
            : `${item.name} ${item.path}`

        if (!searchable.toLowerCase().includes(normalizedSearch)) {
          return false
        }
      }

      if (tagFilterSet.size > 0) {
        if (item.type !== "workflow") {
          return false
        }
        const hasMatchingTag = (item.tags ?? []).some((tag) =>
          tagFilterSet.has(tag.ref)
        )
        if (!hasMatchingTag) {
          return false
        }
      }

      if (hasTriggerFiltersActive) {
        if (item.type !== "workflow") {
          return false
        }

        const triggerSummary = getWorkflowTriggerSummary(item)
        const hasWebhook = Boolean(triggerSummary?.webhook_active)
        const caseTriggerEvents = triggerSummary?.case_trigger_events ?? []

        if (webhookFilter === "enabled" && !hasWebhook) {
          return false
        }
        if (webhookFilter === "disabled" && hasWebhook) {
          return false
        }

        if (!matchesScheduleFilter(triggerSummary, scheduleFilter)) {
          return false
        }

        if (caseAddonsEnabled && caseTriggerFilterSet.size > 0) {
          const hasMatchingCaseTrigger = caseTriggerEvents.some((eventType) =>
            caseTriggerFilterSet.has(eventType)
          )
          if (!hasMatchingCaseTrigger) {
            return false
          }
        }
      }

      return true
    },
    [
      caseAddonsEnabled,
      caseTriggerFilterSet,
      hasTriggerFiltersActive,
      normalizedSearch,
      scheduleFilter,
      tagFilterSet,
      webhookFilter,
    ]
  )

  const listItems = useMemo<WorkflowDirectoryItem[]>(
    () =>
      workflowPagination.data.map((workflow) => ({
        ...workflow,
        type: "workflow",
      })),
    [workflowPagination.data]
  )

  const filteredListItems = useMemo(
    () => listItems.filter((item) => matchesFilters(item)),
    [listItems, matchesFilters]
  )

  const sortedListItems = useMemo(
    () =>
      [...filteredListItems].sort((a, b) => compareItemsBySort(a, b, sortBy)),
    [filteredListItems, sortBy]
  )

  const filteredDirectoryItems = useMemo(
    () => (directoryItems ?? []).filter((item) => matchesFilters(item)),
    [directoryItems, matchesFilters]
  )

  const sortedDirectoryItems = useMemo(
    () =>
      [...filteredDirectoryItems].sort((a, b) =>
        compareItemsBySort(a, b, sortBy)
      ),
    [filteredDirectoryItems, sortBy]
  )

  useEffect(() => {
    if (!caseAddonsEnabled && caseTriggerFilter.length > 0) {
      setCaseTriggerFilter([])
    }
  }, [caseAddonsEnabled, caseTriggerFilter])

  useEffect(() => {
    setFolderPage(0)
  }, [
    caseAddonsEnabled,
    view,
    limit,
    currentPath,
    normalizedSearch,
    tagFilter,
    webhookFilter,
    scheduleFilter,
    caseTriggerFilter,
    sortBy,
  ])

  const folderStartIndex = folderPage * limit
  const folderVisibleItems = useMemo(
    () =>
      sortedDirectoryItems.slice(folderStartIndex, folderStartIndex + limit),
    [sortedDirectoryItems, folderStartIndex, limit]
  )

  const localListFiltersActive =
    normalizedSearch.length > 0 || hasTriggerFiltersActive

  const headerTotalCount =
    view === "folders"
      ? sortedDirectoryItems.length
      : localListFiltersActive
        ? sortedListItems.length
        : workflowPagination.totalEstimate || sortedListItems.length

  const visibleItems = view === "folders" ? folderVisibleItems : sortedListItems
  const isLoading =
    view === "folders" ? directoryItemsIsLoading : workflowPagination.isLoading
  const error =
    view === "folders" ? directoryItemsError : workflowPagination.error

  const hasPreviousPage =
    view === "folders" ? folderPage > 0 : workflowPagination.hasPreviousPage

  const hasNextPage =
    view === "folders"
      ? folderStartIndex + limit < sortedDirectoryItems.length
      : workflowPagination.hasNextPage

  const handlePreviousPage = () => {
    if (view === "folders") {
      setFolderPage((current) => Math.max(current - 1, 0))
      return
    }
    workflowPagination.goToPreviousPage()
  }

  const handleNextPage = () => {
    if (view === "folders") {
      setFolderPage((current) => {
        const maxPage = Math.max(
          Math.ceil(sortedDirectoryItems.length / limit) - 1,
          0
        )
        return Math.min(current + 1, maxPage)
      })
      return
    }
    workflowPagination.goToNextPage()
  }

  const emptyMessage =
    view === "folders"
      ? "No items found in this folder."
      : "No workflows found."

  return (
    <DeleteWorkflowAlertDialog
      selectedWorkflow={selectedWorkflow}
      setSelectedWorkflow={setSelectedWorkflow}
    >
      <TooltipProvider>
        <div className="flex size-full flex-col overflow-hidden">
          <WorkflowsHeader
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            view={view}
            onViewChange={handleViewChange}
            tags={tags}
            tagFilter={tagFilter}
            onTagChange={setTagFilter}
            webhookFilter={webhookFilter}
            onWebhookFilterChange={setWebhookFilter}
            scheduleFilter={scheduleFilter}
            onScheduleFilterChange={setScheduleFilter}
            showCaseTriggerFilter={caseAddonsEnabled}
            caseTriggerFilter={caseTriggerFilter}
            onCaseTriggerFilterChange={setCaseTriggerFilter}
            sortBy={sortBy}
            onSortByChange={setSortBy}
            totalCount={headerTotalCount}
            countLabel="workflows"
            limit={limit}
            onLimitChange={setLimit}
            hasPreviousPage={hasPreviousPage}
            hasNextPage={hasNextPage}
            onPreviousPage={handlePreviousPage}
            onNextPage={handleNextPage}
            isPaginationLoading={isLoading}
          />

          <div className="min-h-0 flex-1 overflow-auto">
            {isLoading ? (
              <div className="flex h-full items-center justify-center">
                <CenteredSpinner />
              </div>
            ) : error ? (
              <div className="flex h-full items-center justify-center px-6">
                <span className="text-sm text-destructive">
                  Failed to load workflows.
                </span>
              </div>
            ) : visibleItems.length === 0 ? (
              <div className="flex h-full items-center justify-center px-6">
                <span className="text-sm text-muted-foreground">
                  {emptyMessage}
                </span>
              </div>
            ) : (
              <div className="divide-y">
                {visibleItems.map((item) => (
                  <WorkflowsListRow
                    key={`${item.type}-${item.id}`}
                    item={item}
                    availableTags={tags}
                    onOpenWorkflow={(workflowId) => {
                      router.push(
                        `/workspaces/${workspaceId}/workflows/${workflowId}`
                      )
                    }}
                    onOpenFolder={handleOpenFolder}
                    setSelectedWorkflow={setSelectedWorkflow}
                    setSelectedFolder={setSelectedFolder}
                    setActiveDialog={setActiveDialog}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </TooltipProvider>

      <FolderDeleteAlertDialog
        open={activeDialog === ActiveDialog.FolderDelete}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
      <FolderRenameDialog
        open={activeDialog === ActiveDialog.FolderRename}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
      <WorkflowMoveDialog
        open={activeDialog === ActiveDialog.WorkflowMove}
        onOpenChange={() => setActiveDialog(null)}
        selectedWorkflow={selectedWorkflow}
        setSelectedWorkflow={setSelectedWorkflow}
      />
      <FolderMoveDialog
        open={activeDialog === ActiveDialog.FolderMove}
        onOpenChange={() => setActiveDialog(null)}
        selectedFolder={selectedFolder}
        setSelectedFolder={setSelectedFolder}
      />
    </DeleteWorkflowAlertDialog>
  )
}
