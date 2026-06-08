"use client"

import {
  type Query,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type AgentSessionEntity,
  type AgentSessionRead,
  agentSessionsListSessions,
  approvalsDeleteApproval,
  type ChatReadMinimal,
  type InboxItemRead,
  type InboxItemStatus,
  inboxListItems,
} from "@/client"
import {
  type AgentDerivedStatus,
  type AgentStatusTone,
  getAgentStatusMetadata,
  type InboxSessionItem,
} from "@/lib/agents"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

export interface UseInboxOptions {
  enabled?: boolean
  autoRefresh?: boolean
}

export type DateFilterValue = "1d" | "3d" | "1w" | "1m" | null
export type InboxStatusFilter =
  | "all"
  | "review_required"
  | "running"
  | "error"
  | "completed"
  | "unknown"

type CombinedInboxData = {
  items: InboxItemRead[]
  approvalRuns: Array<AgentSessionRead | ChatReadMinimal>
}

const INBOX_FETCH_LIMIT = 100

const STATUS_FILTER_STATUSES: Record<
  Exclude<InboxStatusFilter, "all">,
  Set<AgentDerivedStatus>
> = {
  review_required: new Set(["PENDING_APPROVAL"]),
  running: new Set(["RUNNING", "CONTINUED_AS_NEW"]),
  error: new Set(["FAILED", "TIMED_OUT", "TERMINATED"]),
  completed: new Set(["COMPLETED", "CANCELED"]),
  unknown: new Set(["UNKNOWN"]),
}

export interface UseInboxFilters {
  searchQuery: string
  entityType: AgentSessionEntity | "all"
  statusFilter: InboxStatusFilter
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
  setStatusFilter: (status: InboxStatusFilter) => void
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
        statusLabel: "Review required",
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
    source: "inbox_item",
    title: item.title,
    entity_type: (item.metadata?.entity_type as string) ?? "workflow",
    entity_id: (item.metadata?.entity_id as string) ?? null,
    parent_session_id: null,
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

function agentRunToSessionItem(
  session: AgentSessionRead | ChatReadMinimal
): InboxSessionItem {
  const metadata = getAgentStatusMetadata("UNKNOWN")
  const parentSessionId =
    "parent_session_id" in session ? (session.parent_session_id ?? null) : null

  return {
    id: session.id,
    source: "agent_run",
    title: session.title,
    entity_type: session.entity_type,
    entity_id: session.entity_id,
    parent_session_id: parentSessionId,
    created_at: session.created_at,
    updated_at: session.updated_at,
    parent_workflow: null,
    derivedStatus: "UNKNOWN",
    statusLabel: metadata.label,
    statusPriority: metadata.priority,
    statusTone: metadata.tone,
    pendingApprovalCount: 0,
  }
}

function matchesStatusFilter(
  session: InboxSessionItem,
  statusFilter: InboxStatusFilter
): boolean {
  if (statusFilter === "all") {
    return true
  }
  return STATUS_FILTER_STATUSES[statusFilter].has(session.derivedStatus)
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
  const [statusFilter, setStatusFilter] = useState<InboxStatusFilter>("all")
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
        CombinedInboxData,
        TracecatApiError,
        CombinedInboxData,
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

      if (
        !data ||
        (data.items.length === 0 && data.approvalRuns.length === 0)
      ) {
        return 10000
      }

      const hasPendingApproval = data.items.some(
        (item) => item.status === "pending"
      )
      const hasRunningExecution = data.items.some(
        (item) =>
          typeof item.metadata?.temporal_status === "string" &&
          (item.metadata.temporal_status === "RUNNING" ||
            item.metadata.temporal_status === "CONTINUED_AS_NEW")
      )
      if (hasPendingApproval || hasRunningExecution) {
        return 3000
      }

      return 10000
    },
    [autoRefresh]
  )

  // Fetch inbox items from the unified inbox endpoint and show approval
  // continuation agent runs alongside them.
  const {
    data: sessions,
    isLoading,
    error,
    refetch,
  } = useQuery<CombinedInboxData, TracecatApiError, InboxSessionItem[]>({
    queryKey: ["inbox-items", workspaceId],
    queryFn: async () => {
      const inboxResponse = await inboxListItems({
        workspaceId,
        limit: INBOX_FETCH_LIMIT,
      })

      let approvalRuns: Array<AgentSessionRead | ChatReadMinimal> = []
      try {
        approvalRuns = await agentSessionsListSessions({
          workspaceId,
          entityType: "approval",
          limit: INBOX_FETCH_LIMIT,
        })
      } catch (err) {
        console.warn("Failed to load approval agent runs for inbox", err)
      }

      return {
        items: inboxResponse.items,
        approvalRuns,
      }
    },
    select: (data) => {
      // Convert inbox items and approval agent runs to session format.
      const converted = [...data.items.map(inboxItemToSessionItem)]
      const seenIds = new Set(converted.map((item) => item.id))
      for (const approvalRun of data.approvalRuns) {
        if (!seenIds.has(approvalRun.id)) {
          converted.push(agentRunToSessionItem(approvalRun))
          seenIds.add(approvalRun.id)
        }
      }

      // Sort by status priority (review-required items first), then by updated_at (most recent first)
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

  // Apply client-side filtering (search, status, entity type, date filters)
  const filteredSessions = useMemo(() => {
    if (!sessions) return []

    const updatedAfterDate = getDateFromFilter(updatedAfter)
    const createdAfterDate = getDateFromFilter(createdAfter)
    const query = searchQuery.toLowerCase().trim()

    return sessions
      .filter((session) => {
        // Entity type filter
        if (entityType !== "all" && session.entity_type !== entityType) {
          return false
        }

        if (!matchesStatusFilter(session, statusFilter)) {
          return false
        }

        // Search by display name, session title, workflow name, or entity ID.
        if (query) {
          const displayName = (
            session.parent_workflow?.alias ||
            session.parent_workflow?.title ||
            session.title ||
            ""
          ).toLowerCase()
          const title = (session.title || "").toLowerCase()
          const workflowTitle = (
            session.parent_workflow?.title || ""
          ).toLowerCase()
          const workflowAlias = (
            session.parent_workflow?.alias || ""
          ).toLowerCase()
          const entityId = (session.entity_id || "").toLowerCase()
          if (
            !displayName.includes(query) &&
            !title.includes(query) &&
            !workflowTitle.includes(query) &&
            !workflowAlias.includes(query) &&
            !entityId.includes(query)
          ) {
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
      .slice(0, limit)
  }, [
    sessions,
    searchQuery,
    entityType,
    statusFilter,
    updatedAfter,
    createdAfter,
    limit,
  ])

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
      statusFilter,
      limit,
      updatedAfter,
      createdAfter,
    },
    setSearchQuery,
    setEntityType,
    setStatusFilter,
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
