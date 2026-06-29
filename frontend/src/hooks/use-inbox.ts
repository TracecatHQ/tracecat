"use client"

import {
  type InfiniteData,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import {
  type AgentSessionEntity,
  approvalsDeleteApproval,
  type InboxGroup,
  type InboxItemRead,
  type InboxItemStatus,
  type InboxListItemsResponse,
  inboxListItems,
} from "@/client"
import { useDebounce } from "@/hooks/use-debounce"
import {
  type AgentDerivedStatus,
  type AgentStatusTone,
  getAgentStatusMetadata,
  type InboxSessionItem,
} from "@/lib/agents"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Columns the inbox API can sort on globally (server-side keyset order). */
export type InboxOrderBy = "created_at" | "updated_at"

export interface UseInboxOptions {
  enabled?: boolean
  autoRefresh?: boolean
  orderBy?: InboxOrderBy
  sort?: "asc" | "desc"
}

export type DateFilterValue = "1d" | "3d" | "1w" | "1m" | null

export interface UseInboxFilters {
  searchQuery: string
  entityType: AgentSessionEntity | "all"
  limit: number
  updatedAfter: DateFilterValue
  createdAfter: DateFilterValue
}

/** Display order of inbox status groups, most urgent first. */
export const INBOX_GROUP_ORDER: InboxGroup[] = [
  "review_required",
  "running",
  "error",
  "completed",
]

/** Paginated state of a single inbox status group. */
export interface InboxGroupState {
  sessions: InboxSessionItem[]
  isLoading: boolean
  hasMore: boolean
  isLoadingMore: boolean
  loadMore: () => void
}

export interface UseInboxResult {
  sessions: InboxSessionItem[]
  groups: Record<InboxGroup, InboxGroupState>
  selectedId: string | null
  setSelectedId: (id: string | null) => void
  isLoading: boolean
  error: Error | null
  refetch: () => void
  filters: UseInboxFilters
  setSearchQuery: (query: string) => void
  setEntityType: (type: AgentSessionEntity | "all") => void
  setLimit: (limit: number) => void
  setUpdatedAfter: (value: DateFilterValue) => void
  setCreatedAfter: (value: DateFilterValue) => void
}

/**
 * Maps InboxItemStatus to the derived status and display properties used by the UI.
 */
const TEMPORAL_DERIVED_STATUSES = new Set<AgentDerivedStatus>([
  "RUNNING",
  "CONTINUED_AS_NEW",
  "FAILED",
  "TIMED_OUT",
  "TERMINATED",
  "COMPLETED",
  "CANCELED",
  "UNKNOWN",
])

function mapInboxStatusToAgentStatus(
  status: InboxItemStatus,
  temporalStatusRaw: string | null
): {
  derivedStatus: InboxSessionItem["derivedStatus"]
  statusLabel: string
  statusPriority: number
  statusTone: AgentStatusTone
} {
  if (
    temporalStatusRaw &&
    TEMPORAL_DERIVED_STATUSES.has(temporalStatusRaw as AgentDerivedStatus) &&
    status !== "pending"
  ) {
    const temporalStatus = temporalStatusRaw as AgentDerivedStatus
    const metadata = getAgentStatusMetadata(temporalStatus)
    return {
      derivedStatus: temporalStatus,
      statusLabel: metadata.label,
      statusPriority: metadata.priority,
      statusTone: metadata.tone,
    }
  }
  switch (status) {
    case "pending":
      return {
        derivedStatus: "PENDING_APPROVAL",
        statusLabel: "Pending approvals",
        statusPriority: 0,
        statusTone: "warning",
      }
    case "failed":
      return {
        derivedStatus: "FAILED",
        statusLabel: "Failed",
        statusPriority: 1,
        statusTone: "danger",
      }
    case "completed":
      return {
        derivedStatus: "COMPLETED",
        statusLabel: "Completed",
        statusPriority: 7,
        statusTone: "success",
      }
    default:
      return {
        derivedStatus: "UNKNOWN",
        statusLabel: "Unknown",
        statusPriority: 8,
        statusTone: "neutral",
      }
  }
}

/**
 * Converts an InboxItemRead to the InboxSessionItem format expected by UI components.
 * This bridges the inbox API with inbox UI components.
 */
function inboxItemToSessionItem(item: InboxItemRead): InboxSessionItem {
  const temporalStatusRaw =
    typeof item.metadata?.temporal_status === "string"
      ? item.metadata.temporal_status
      : null
  const statusInfo = mapInboxStatusToAgentStatus(item.status, temporalStatusRaw)

  return {
    // Core session fields - use source_id as the session identifier
    id: item.source_id,
    title: item.title,
    entity_type: (item.metadata?.entity_type as string) ?? "workflow",
    entity_id: (item.metadata?.entity_id as string) ?? null,
    created_at: item.created_at,
    updated_at: item.updated_at,

    // Workflow metadata from inbox item
    parent_workflow: item.workflow
      ? {
          id: item.workflow.id,
          title: item.workflow.title,
          alias: item.workflow.alias,
        }
      : null,
    created_by: item.created_by ?? null,

    // Status fields derived from inbox status
    ...statusInfo,
    pendingApprovalCount:
      (item.metadata?.pending_count as number) ??
      (item.status === "pending" ? 1 : 0),
  }
}

const DAY_MS = 24 * 60 * 60 * 1000

const DATE_FILTER_DAYS: Record<NonNullable<DateFilterValue>, number> = {
  "1d": 1,
  "3d": 3,
  "1w": 7,
  "1m": 30,
}

/**
 * Resolves a relative date filter to an absolute cutoff, relative to now.
 *
 * Pin the result once per selection (e.g. in a `useMemo` keyed on the
 * `DateFilterValue` token) — never call this inside `queryFn`. The returned
 * timestamp depends on the current time, so recomputing it on every poll would
 * walk the cutoff forward each tick and silently drop rows that sit just behind
 * the boundary, even though the UI still reads "1 day ago".
 */
function getDateFromFilter(filter: DateFilterValue): string | null {
  if (!filter) return null
  return new Date(Date.now() - DATE_FILTER_DAYS[filter] * DAY_MS).toISOString()
}

interface InboxGroupQueryOptions {
  workspaceId: string
  group: InboxGroup
  limit: number
  search: string
  entityType: AgentSessionEntity | "all"
  /** ISO cutoff pinned at filter-selection time, not at fetch time. */
  updatedAfterIso: string | null
  /** ISO cutoff pinned at filter-selection time, not at fetch time. */
  createdAfterIso: string | null
  orderBy: InboxOrderBy
  sort: "asc" | "desc"
  enabled: boolean
  autoRefresh: boolean
  pollMs: number
}

/**
 * Infinite query over a single inbox status group.
 *
 * Pages are appended ("show more") rather than replaced, and polling pauses
 * when auto-refresh is off or the tab is hidden.
 */
function useInboxGroupQuery({
  workspaceId,
  group,
  limit,
  search,
  entityType,
  updatedAfterIso,
  createdAfterIso,
  orderBy,
  sort,
  enabled,
  autoRefresh,
  pollMs,
}: InboxGroupQueryOptions) {
  // The server applies entity-type and date filters in its keyset selection, so
  // they belong in the query key: changing a filter must restart the cursor
  // stream rather than reuse pages chosen under the old filter. The date cutoffs
  // are ISO strings already pinned at selection time (see getDateFromFilter), so
  // keying on them is stable across polls — the key only churns when the user
  // actually changes the filter.
  const entityTypeParam = entityType === "all" ? null : entityType
  return useInfiniteQuery<
    InboxListItemsResponse,
    TracecatApiError,
    InfiniteData<InboxListItemsResponse>,
    readonly unknown[],
    string | null
  >({
    queryKey: [
      "inbox-items",
      workspaceId,
      group,
      limit,
      search,
      entityTypeParam,
      updatedAfterIso,
      createdAfterIso,
      orderBy,
      sort,
    ],
    queryFn: ({ pageParam }) =>
      inboxListItems({
        workspaceId,
        limit,
        cursor: pageParam,
        search: search || null,
        group,
        entityType: entityTypeParam,
        updatedAfter: updatedAfterIso,
        createdAfter: createdAfterIso,
        orderBy,
        sort,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.next_cursor ? lastPage.next_cursor : null,
    enabled,
    retry: retryHandler,
    refetchInterval: (query) => {
      if (!autoRefresh) {
        return false
      }
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        return false
      }
      // Only poll when a single page is loaded; additional pages from "show
      // more" would each be re-fetched on every tick, causing request fan-out.
      const pageCount = query.state.data?.pages.length ?? 0
      if (pageCount > 1) {
        return false
      }
      return pollMs
    },
    placeholderData: keepPreviousData,
  })
}

export function useInbox(options: UseInboxOptions = {}): UseInboxResult {
  const {
    enabled = true,
    autoRefresh = true,
    orderBy = "updated_at",
    sort = "desc",
  } = options
  const workspaceId = useWorkspaceId()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [debouncedSearchQuery] = useDebounce(searchQuery, 300)
  const normalizedSearchQuery = debouncedSearchQuery.trim()
  const [entityType, setEntityType] = useState<AgentSessionEntity | "all">(
    "all"
  )
  const [limit, setLimit] = useState(20)
  const [updatedAfter, setUpdatedAfter] = useState<DateFilterValue>(null)
  const [createdAfter, setCreatedAfter] = useState<DateFilterValue>(null)

  // Pin the absolute cutoff once per selection. Recomputing it on every poll
  // would creep the boundary forward each tick (rows just behind it would
  // vanish); keying the memo on the token freezes the cutoff until the user
  // changes the filter.
  const updatedAfterIso = useMemo(
    () => getDateFromFilter(updatedAfter),
    [updatedAfter]
  )
  const createdAfterIso = useMemo(
    () => getDateFromFilter(createdAfter),
    [createdAfter]
  )

  const baseEnabled = enabled && Boolean(workspaceId)

  // One independently paginated query per status group. Urgent groups poll
  // faster; terminal groups poll slowly.
  const reviewRequiredQuery = useInboxGroupQuery({
    workspaceId,
    group: "review_required",
    limit,
    search: normalizedSearchQuery,
    entityType,
    updatedAfterIso,
    createdAfterIso,
    orderBy,
    sort,
    enabled: baseEnabled,
    autoRefresh,
    pollMs: 3000,
  })
  const runningQuery = useInboxGroupQuery({
    workspaceId,
    group: "running",
    limit,
    search: normalizedSearchQuery,
    entityType,
    updatedAfterIso,
    createdAfterIso,
    orderBy,
    sort,
    enabled: baseEnabled,
    autoRefresh,
    pollMs: 3000,
  })
  const errorQuery = useInboxGroupQuery({
    workspaceId,
    group: "error",
    limit,
    search: normalizedSearchQuery,
    entityType,
    updatedAfterIso,
    createdAfterIso,
    orderBy,
    sort,
    enabled: baseEnabled,
    autoRefresh,
    pollMs: 10000,
  })
  const completedQuery = useInboxGroupQuery({
    workspaceId,
    group: "completed",
    limit,
    search: normalizedSearchQuery,
    entityType,
    updatedAfterIso,
    createdAfterIso,
    orderBy,
    sort,
    enabled: baseEnabled,
    autoRefresh,
    pollMs: 10000,
  })

  // Entity-type and date filters are applied server-side (in the keyset
  // selection), so groups need no client-side post-filtering here. Doing it on
  // the client would drop rows from an already-chosen page, making groups look
  // short/empty and leaving hasMore tied to the unfiltered page.
  const groupQueries = {
    review_required: reviewRequiredQuery,
    running: runningQuery,
    error: errorQuery,
    completed: completedQuery,
  } as const

  const groups = useMemo<Record<InboxGroup, InboxGroupState>>(() => {
    function toGroupState(
      query: (typeof groupQueries)[InboxGroup]
    ): InboxGroupState {
      const items = query.data?.pages.flatMap((page) => page.items) ?? []
      return {
        sessions: items.map(inboxItemToSessionItem),
        isLoading: query.isLoading,
        hasMore: query.hasNextPage,
        isLoadingMore: query.isFetchingNextPage,
        loadMore: () => {
          void query.fetchNextPage()
        },
      }
    }
    return {
      review_required: toGroupState(groupQueries.review_required),
      running: toGroupState(groupQueries.running),
      error: toGroupState(groupQueries.error),
      completed: toGroupState(groupQueries.completed),
    }
  }, [
    groupQueries.review_required,
    groupQueries.running,
    groupQueries.error,
    groupQueries.completed,
  ])

  const isLoading = INBOX_GROUP_ORDER.some(
    (group) => groupQueries[group].isLoading
  )
  const allErrors = INBOX_GROUP_ORDER.map(
    (group) => groupQueries[group].error
  ).filter(Boolean)
  const error = allErrors[0] ?? null
  const refetch = () => {
    for (const group of INBOX_GROUP_ORDER) {
      void groupQueries[group].refetch()
    }
  }

  // Flatten groups in display order for selection bookkeeping
  const enrichedSessions = useMemo(
    () => INBOX_GROUP_ORDER.flatMap((group) => groups[group].sessions),
    [groups]
  )

  // Auto-select first session with pending approval, or clear stale selections
  useEffect(() => {
    if (enrichedSessions.length === 0) {
      // Clear selection when list becomes empty
      if (selectedId !== null) {
        setSelectedId(null)
      }
      return
    }

    // Check if current selection still exists in the list
    const selectionExists =
      selectedId !== null &&
      enrichedSessions.some((session) => session.id === selectedId)

    if (!selectionExists && selectedId !== null) {
      // Clear stale selection to avoid inconsistent UI state
      // Don't auto-select a new session - let user choose
      setSelectedId(null)
    }
  }, [enrichedSessions, selectedId])

  return {
    sessions: enrichedSessions,
    groups,
    selectedId,
    setSelectedId,
    isLoading,
    error: error ?? null,
    refetch,
    filters: {
      searchQuery,
      entityType,
      limit,
      updatedAfter,
      createdAfter,
    },
    setSearchQuery,
    setEntityType,
    setLimit,
    setUpdatedAfter,
    setCreatedAfter,
  }
}

export function useDeleteApproval() {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (sessionId: string) =>
      approvalsDeleteApproval({ sessionId, workspaceId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inbox-items"] })
      // Keep the sidebar pending-approvals badge in sync immediately rather than
      // waiting for its independent poll/window-focus refetch.
      queryClient.invalidateQueries({
        queryKey: ["pending-approvals-count", workspaceId],
      })
    },
  })
}
