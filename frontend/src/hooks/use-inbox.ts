"use client"

import { type Query, useQuery } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type AgentSessionEntity,
  type InboxItemRead,
  type InboxItemStatus,
  type InboxListItemsResponse,
  inboxListItems,
} from "@/client"
import type { AgentStatusTone, InboxSessionItem } from "@/lib/agents"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

export interface UseInboxOptions {
  enabled?: boolean
  autoRefresh?: boolean
}

export type DateFilterValue = "1d" | "3d" | "1w" | "1m" | null

export interface UseInboxFilters {
  searchQuery: string
  entityType: AgentSessionEntity | "all"
  limit: number
  updatedAfter: DateFilterValue
  createdAfter: DateFilterValue
}

export interface UseInboxResult {
  sessions: InboxSessionItem[]
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
function mapInboxStatusToAgentStatus(status: InboxItemStatus): {
  derivedStatus: InboxSessionItem["derivedStatus"]
  statusLabel: string
  statusPriority: number
  statusTone: AgentStatusTone
} {
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
  }
}

/**
 * Converts an InboxItemRead to the InboxSessionItem format expected by UI components.
 * This bridges the inbox API with inbox UI components.
 */
function inboxItemToSessionItem(item: InboxItemRead): InboxSessionItem {
  const statusInfo = mapInboxStatusToAgentStatus(item.status)

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

export function useInbox(options: UseInboxOptions = {}): UseInboxResult {
  const { enabled = true, autoRefresh = true } = options
  const workspaceId = useWorkspaceId()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [entityType, setEntityType] = useState<AgentSessionEntity | "all">(
    "all"
  )
  const [limit, setLimit] = useState(20)
  const [updatedAfter, setUpdatedAfter] = useState<DateFilterValue>(null)
  const [createdAfter, setCreatedAfter] = useState<DateFilterValue>(null)

  /**
   * Computes the refetch interval for inbox items based on current state.
   *
   * Returns `false` to disable polling when:
   * - Auto-refresh is disabled
   * - The browser tab is hidden
   *
   * Otherwise returns an interval in milliseconds:
   * - 3000ms (3s): When there are pending approvals
   * - 10000ms (10s): When no items exist or all are in terminal states
   */
  const computeRefetchInterval = useCallback(
    (
      query: Query<
        InboxListItemsResponse,
        TracecatApiError,
        InboxListItemsResponse,
        readonly unknown[]
      >
    ) => {
      if (!autoRefresh) {
        return false
      }

      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        return false
      }

      const data = query.state.data

      if (!data || data.items.length === 0) {
        return 10000
      }

      const hasPendingApproval = data.items.some(
        (item) => item.status === "pending"
      )
      if (hasPendingApproval) {
        return 3000
      }

      return 10000
    },
    [autoRefresh]
  )

  // Fetch inbox items from the unified inbox endpoint
  // This endpoint properly aggregates approval status from the backend
  const {
    data: sessions,
    isLoading,
    error,
    refetch,
  } = useQuery<InboxListItemsResponse, TracecatApiError, InboxSessionItem[]>({
    queryKey: ["inbox-items", workspaceId, limit],
    queryFn: () =>
      inboxListItems({
        workspaceId,
        limit,
      }),
    select: (data) => {
      // Convert inbox items to session format and sort by priority
      const converted = data.items.map(inboxItemToSessionItem)
      // Sort by status priority (pending approvals first), then by updated_at (most recent first)
      return converted.sort((a, b) => {
        if (a.statusPriority !== b.statusPriority) {
          return a.statusPriority - b.statusPriority
        }
        return (
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        )
      })
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
    refetchInterval: computeRefetchInterval,
  })

  // Apply client-side filtering (search, entity type, date filters)
  const filteredSessions = useMemo(() => {
    if (!sessions) return []

    const updatedAfterDate = getDateFromFilter(updatedAfter)
    const createdAfterDate = getDateFromFilter(createdAfter)
    const query = searchQuery.toLowerCase().trim()

    return sessions.filter((session) => {
      // Entity type filter
      if (entityType !== "all" && session.entity_type !== entityType) {
        return false
      }

      // Search filter
      if (query) {
        const title = (
          session.parent_workflow?.alias ||
          session.parent_workflow?.title ||
          session.title ||
          ""
        ).toLowerCase()
        const entityId = (session.entity_id || "").toLowerCase()
        if (!title.includes(query) && !entityId.includes(query)) {
          return false
        }
      }

      // Updated after filter
      if (updatedAfterDate) {
        const sessionUpdated = new Date(session.updated_at)
        if (sessionUpdated < updatedAfterDate) {
          return false
        }
      }

      // Created after filter
      if (createdAfterDate) {
        const sessionCreated = new Date(session.created_at)
        if (sessionCreated < createdAfterDate) {
          return false
        }
      }

      return true
    })
  }, [sessions, searchQuery, entityType, updatedAfter, createdAfter])

  const enrichedSessions = filteredSessions

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
