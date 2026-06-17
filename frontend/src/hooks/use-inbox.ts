"use client"

import {
  type InfiniteData,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
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

function getDateFromFilter(filter: DateFilterValue): Date | null {
  if (!filter) return null
  const now = new Date()
  switch (filter) {
    case "1d":
      return new Date(now.getTime() - 24 * 60 * 60 * 1000)
    case "3d":
      return new Date(now.getTime() - 3 * 24 * 60 * 60 * 1000)
    case "1w":
      return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
    case "1m":
      return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)
    default:
      return null
  }
}

interface InboxGroupQueryOptions {
  workspaceId: string
  group: InboxGroup
  limit: number
  search: string
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
  orderBy,
  sort,
  enabled,
  autoRefresh,
  pollMs,
}: InboxGroupQueryOptions) {
  return useInfiniteQuery<
    InboxListItemsResponse,
    TracecatApiError,
    InfiniteData<InboxListItemsResponse>,
    readonly unknown[],
    string | null
  >({
    queryKey: ["inbox-items", workspaceId, group, limit, search, orderBy, sort],
    queryFn: ({ pageParam }) =>
      inboxListItems({
        workspaceId,
        limit,
        cursor: pageParam,
        search: search || null,
        group,
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

  const baseEnabled = enabled && Boolean(workspaceId)

  // One independently paginated query per status group. Urgent groups poll
  // faster; terminal groups poll slowly.
  const reviewRequiredQuery = useInboxGroupQuery({
    workspaceId,
    group: "review_required",
    limit,
    search: normalizedSearchQuery,
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
    orderBy,
    sort,
    enabled: baseEnabled,
    autoRefresh,
    pollMs: 10000,
  })

  // Client-side filtering (entity type, date filters) applied per group
  const filterSession = useCallback(
    (session: InboxSessionItem) => {
      const updatedAfterDate = getDateFromFilter(updatedAfter)
      const createdAfterDate = getDateFromFilter(createdAfter)

      if (entityType !== "all" && session.entity_type !== entityType) {
        return false
      }
      if (updatedAfterDate && new Date(session.updated_at) < updatedAfterDate) {
        return false
      }
      if (createdAfterDate && new Date(session.created_at) < createdAfterDate) {
        return false
      }
      return true
    },
    [entityType, updatedAfter, createdAfter]
  )

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
        sessions: items.map(inboxItemToSessionItem).filter(filterSession),
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
    filterSession,
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
    },
  })
}
