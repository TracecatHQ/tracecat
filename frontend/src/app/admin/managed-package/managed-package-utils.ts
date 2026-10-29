import type {
  GitTagInfo,
  ManagedReferenceKind,
  ManagedRolloutStatus,
  ManagedTargetStatus,
  SyncResourceType,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"

/** Release tags must look like `v1.2.3`; the backend enforces the same rule. */
export const RELEASE_TAG_PATTERN = /^v\d+\.\d+\.\d+$/

/** Matches a canonical UUID string. */
export const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Rollout statuses that mean background work is still running. */
export const ACTIVE_ROLLOUT_STATUSES: ReadonlySet<ManagedRolloutStatus> =
  new Set(["previewing", "applying"])

/** Rollout statuses the apply endpoint will act on. */
export const APPLICABLE_ROLLOUT_STATUSES: ReadonlySet<ManagedRolloutStatus> =
  new Set(["ready", "no_changes"])

/** Badge styling for a rollout or target status. */
export type StatusTone = "neutral" | "success" | "warning" | "danger"

const ROLLOUT_STATUS_LABELS: Record<ManagedRolloutStatus, string> = {
  previewing: "Previewing",
  ready: "Ready",
  no_changes: "No changes",
  blocked: "Blocked",
  needs_references: "Needs references",
  applying: "Applying",
  applied: "Applied",
  failed: "Failed",
  stale: "Stale",
}

const ROLLOUT_STATUS_TONES: Record<ManagedRolloutStatus, StatusTone> = {
  previewing: "neutral",
  ready: "success",
  no_changes: "neutral",
  blocked: "danger",
  needs_references: "warning",
  applying: "neutral",
  applied: "success",
  failed: "danger",
  stale: "neutral",
}

const TARGET_STATUS_LABELS: Record<ManagedTargetStatus, string> = {
  pending: "Pending",
  up_to_date: "Up to date",
  blocked: "Blocked",
  needs_references: "Needs references",
  failed: "Failed",
}

const TARGET_STATUS_TONES: Record<ManagedTargetStatus, StatusTone> = {
  pending: "neutral",
  up_to_date: "success",
  blocked: "danger",
  needs_references: "warning",
  failed: "danger",
}

/** Label and tone for a rollout status. */
export function rolloutStatusDisplay(status: ManagedRolloutStatus): {
  label: string
  tone: StatusTone
} {
  return {
    label: ROLLOUT_STATUS_LABELS[status],
    tone: ROLLOUT_STATUS_TONES[status],
  }
}

/** Label and tone for a target's last rollout status. */
export function targetStatusDisplay(status: ManagedTargetStatus): {
  label: string
  tone: StatusTone
} {
  return {
    label: TARGET_STATUS_LABELS[status],
    tone: TARGET_STATUS_TONES[status],
  }
}

const REFERENCE_KIND_LABELS: Record<ManagedReferenceKind, string> = {
  model: "Model",
  mcp_integration: "MCP integration",
  secret_store: "Secret store",
}

/** Human label for a reference kind. */
export function referenceKindLabel(kind: ManagedReferenceKind): string {
  return REFERENCE_KIND_LABELS[kind]
}

/** Human label for a synced resource type, e.g. `agent_preset` -> "Agent preset". */
export function resourceTypeLabel(type: SyncResourceType): string {
  const words = type.replaceAll("_", " ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * Position of a tag in the newest-first tag list, or -1 when unknown.
 * A larger index means an older tag.
 */
export function tagAge(tags: GitTagInfo[], tagName: string | null): number {
  if (!tagName) {
    return -1
  }
  return tags.findIndex((tag) => tag.name === tagName)
}

/** Format an ISO timestamp for tables, or a dash when missing. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "-"
  }
  return new Date(value).toLocaleString()
}

/** Show a destructive toast with the API's error detail when there is one. */
export function toastApiError(title: string, error: unknown): void {
  console.error(title, error)
  toast({
    title,
    description: getApiErrorDetail(error) ?? "Please try again.",
    variant: "destructive",
  })
}
