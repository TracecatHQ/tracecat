import type { UIMessage } from "ai"
import type {
  AgentSessionRead,
  ApprovalRead,
  ChatReadMinimal,
  WorkflowExecutionStatus,
} from "@/client"
import { undoSlugify } from "@/lib/utils"

export type WorkflowSummary = {
  id: string
  title: string
  alias?: string | null
}

/**
 * Base session type that can be either AgentSessionRead or ChatReadMinimal.
 * Used for backward compatibility with legacy Chat records.
 */
export type SessionBase = AgentSessionRead | ChatReadMinimal

/**
 * Extended session with optional metadata fields populated by the workflow API.
 */
export type AgentSessionReadWithMeta = SessionBase & {
  parent_workflow?: WorkflowSummary | null
  root_workflow?: WorkflowSummary | null
  action_ref?: string | null
  action_title?: string | null
  approvals?: ApprovalRead[] | null
  status?: WorkflowExecutionStatus | null
  parent_id?: string | null
  parent_run_id?: string | null
  root_id?: string | null
  root_run_id?: string | null
}

export function isUIMessageArray(value: unknown): value is UIMessage[] {
  if (!Array.isArray(value)) {
    return false
  }
  return value.every((item) => {
    if (!item || typeof item !== "object") {
      return false
    }
    const candidate = item as {
      id?: unknown
      role?: unknown
      parts?: unknown
    }
    return (
      typeof candidate.id === "string" &&
      typeof candidate.role === "string" &&
      Array.isArray(candidate.parts)
    )
  })
}

const TEMPORAL_STATUS_MAP: Record<
  WorkflowExecutionStatus,
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELED"
  | "TERMINATED"
  | "CONTINUED_AS_NEW"
  | "TIMED_OUT"
> = {
  1: "RUNNING",
  2: "COMPLETED",
  3: "FAILED",
  4: "CANCELED",
  5: "TERMINATED",
  6: "CONTINUED_AS_NEW",
  7: "TIMED_OUT",
}

export type AgentTemporalStatus =
  (typeof TEMPORAL_STATUS_MAP)[WorkflowExecutionStatus]

export type AgentDerivedStatus =
  | "PENDING_APPROVAL"
  | AgentTemporalStatus
  | "UNKNOWN"

export type AgentStatusTone =
  | "danger"
  | "warning"
  | "success"
  | "info"
  | "neutral"

export interface AgentStatusMetadata {
  label: string
  priority: number
  tone: AgentStatusTone
}

function baseStatusMeta(
  status: AgentTemporalStatus,
  priority: number,
  tone: AgentStatusTone
): AgentStatusMetadata {
  return {
    label: undoSlugify(status.toLowerCase()),
    priority,
    tone,
  }
}

const STATUS_METADATA: Record<AgentDerivedStatus, AgentStatusMetadata> = {
  PENDING_APPROVAL: {
    label: "Pending approvals",
    priority: 0,
    tone: "warning",
  },
  FAILED: baseStatusMeta("FAILED", 1, "danger"),
  TIMED_OUT: baseStatusMeta("TIMED_OUT", 2, "danger"),
  TERMINATED: baseStatusMeta("TERMINATED", 3, "danger"),
  CANCELED: baseStatusMeta("CANCELED", 4, "neutral"),
  RUNNING: baseStatusMeta("RUNNING", 5, "info"),
  CONTINUED_AS_NEW: baseStatusMeta("CONTINUED_AS_NEW", 6, "info"),
  COMPLETED: baseStatusMeta("COMPLETED", 7, "success"),
  UNKNOWN: {
    label: "Unknown",
    priority: 8,
    tone: "neutral",
  },
}

export type AgentSessionWithStatus = AgentSessionReadWithMeta & {
  derivedStatus: AgentDerivedStatus
  statusLabel: string
  statusPriority: number
  statusTone: AgentStatusTone
  pendingApprovalCount: number
  temporalStatus: AgentTemporalStatus | null
}

/**
 * Minimal inbox item type for the inbox list view.
 * This is a simpler type that works with the unified inbox API
 * while maintaining compatibility with inbox UI components.
 */
export interface InboxSessionItem {
  /** The agent session ID (source_id from inbox API) */
  id: string
  title: string
  entity_type: string
  entity_id: string | null
  created_at: string
  updated_at: string
  parent_workflow: WorkflowSummary | null
  derivedStatus: AgentDerivedStatus
  statusLabel: string
  statusPriority: number
  statusTone: AgentStatusTone
  pendingApprovalCount: number
}

export function enrichAgentSession(
  session: AgentSessionReadWithMeta
): AgentSessionWithStatus {
  const pendingApprovalCount =
    session.approvals?.filter((approval) => approval.status === "pending")
      .length ?? 0

  if (pendingApprovalCount > 0) {
    const pendingMeta = STATUS_METADATA["PENDING_APPROVAL"]
    return {
      ...session,
      derivedStatus: "PENDING_APPROVAL",
      statusLabel: pendingMeta.label,
      statusPriority: pendingMeta.priority,
      statusTone: pendingMeta.tone,
      pendingApprovalCount,
      temporalStatus: null,
    }
  }

  const temporalStatus =
    session.status != null
      ? (TEMPORAL_STATUS_MAP[session.status] ?? null)
      : null
  const derivedStatus: AgentDerivedStatus = temporalStatus ?? "UNKNOWN"
  const metadata = STATUS_METADATA[derivedStatus]

  return {
    ...session,
    derivedStatus,
    statusLabel: metadata.label,
    statusPriority: metadata.priority,
    statusTone: metadata.tone,
    pendingApprovalCount,
    temporalStatus,
  }
}

export function getAgentStatusMetadata(
  status: AgentDerivedStatus
): AgentStatusMetadata {
  return STATUS_METADATA[status]
}

export function compareAgentStatusPriority(
  a: AgentDerivedStatus,
  b: AgentDerivedStatus
) {
  return (
    (STATUS_METADATA[a]?.priority ?? Number.MAX_SAFE_INTEGER) -
    (STATUS_METADATA[b]?.priority ?? Number.MAX_SAFE_INTEGER)
  )
}

export type ToolApprovedPayload = {
  kind: "tool-approved"
  override_args?: Record<string, unknown> | null
}

export type ToolDeniedPayload = {
  kind: "tool-denied"
  message?: string
}

export type AgentApprovalDecisionPayload =
  | boolean
  | ToolApprovedPayload
  | ToolDeniedPayload
