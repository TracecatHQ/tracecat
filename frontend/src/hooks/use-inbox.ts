"use client"

import { type Query, useQuery } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type AgentSessionEntity,
  type AgentSessionsListSessionsResponse,
  agentSessionsListSessions,
} from "@/client"
import { type AgentSessionWithStatus, enrichAgentSession } from "@/lib/agents"
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
  sessions: AgentSessionWithStatus[]
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
   * Computes the refetch interval for sessions based on current state.
   *
   * Returns `false` to disable polling when:
   * - Auto-refresh is disabled
   * - The browser tab is hidden
   *
   * Otherwise returns an interval in milliseconds:
   * - 3000ms (3s): When there are pending approvals or running sessions
   * - 10000ms (10s): When no sessions exist or all are in terminal states
   */
  const computeRefetchInterval = useCallback(
    (
      query: Query<
        AgentSessionsListSessionsResponse,
        TracecatApiError,
        AgentSessionsListSessionsResponse,
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

      if (!data || data.length === 0) {
        return 10000
      }

      const enrichedSessions = data.map(enrichAgentSession)

      const hasPendingApproval = enrichedSessions.some(
        (session) => session.pendingApprovalCount > 0
      )
      if (hasPendingApproval) {
        return 3000
      }

      const hasActiveSession = enrichedSessions.some((session) =>
        ["RUNNING", "CONTINUED_AS_NEW"].includes(session.derivedStatus)
      )
      if (hasActiveSession) {
        return 3000
      }

      return 10000
    },
    [autoRefresh]
  )

  // Fetch all sessions (excluding approval sessions which are forks)
  const {
    data: sessions,
    isLoading,
    error,
    refetch,
  } = useQuery<
    AgentSessionsListSessionsResponse,
    TracecatApiError,
    AgentSessionWithStatus[]
  >({
    queryKey: [
      "inbox-sessions",
      workspaceId,
      entityType === "all" ? null : entityType,
      limit,
    ],
    queryFn: () =>
      agentSessionsListSessions({
        workspaceId,
        // Exclude approval sessions (they are forked sessions for inbox replies)
        excludeEntityTypes: ["approval"],
        entityType: entityType === "all" ? undefined : entityType,
        limit,
      }),
    select: (data) => {
      // Enrich sessions with derived status and sort by priority
      const enriched = data.map(enrichAgentSession)
      // Sort by status priority (pending approvals first), then by updated_at (most recent first)
      return enriched.sort((a, b) => {
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

  // Apply client-side filtering (search, date filters)
  const filteredSessions = useMemo(() => {
    if (!sessions) return []

    const updatedAfterDate = getDateFromFilter(updatedAfter)
    const createdAfterDate = getDateFromFilter(createdAfter)
    const query = searchQuery.toLowerCase().trim()

    return sessions.filter((session) => {
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
  }, [sessions, searchQuery, updatedAfter, createdAfter])

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

    if (!selectionExists) {
      // Don't auto-select - let user choose
      // This differs from the old behavior where we auto-selected
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
