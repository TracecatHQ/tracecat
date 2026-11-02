import type { FormErrorCode, FormErrorDetail } from "@/client"
import type { FormConfigIssue } from "@/lib/forms/yaml-diagnostics"

/** The status and body `detail` of a failed generated-client request. */
function readApiError(error: unknown): { status?: unknown; detail?: unknown } {
  if (typeof error !== "object" || error === null) return {}
  const { status, body } = error as {
    status?: unknown
    body?: { detail?: unknown } | null
  }
  return { status, detail: body?.detail }
}

/** Read the typed `detail` of a failed forms request, if it carries one. */
export function getFormErrorDetail(error: unknown): FormErrorDetail | null {
  const detail = readApiError(error).detail as Partial<FormErrorDetail> | null
  return typeof detail?.code === "string" && typeof detail.message === "string"
    ? (detail as FormErrorDetail)
    : null
}

/** Read the machine-readable code of a failed forms request. */
export function getFormErrorCode(error: unknown): FormErrorCode | null {
  return getFormErrorDetail(error)?.code ?? null
}

const OFFLINE_CODES: ReadonlySet<FormErrorCode> = new Set([
  "form_offline",
  "form_unavailable",
  "form_workflow_unpublished",
])

/** Whether a failure means the form is not taking submissions right now. */
export function isFormOfflineError(error: unknown): boolean {
  const code = getFormErrorCode(error)
  return code !== null && OFFLINE_CODES.has(code)
}

/** Why a form could not be shown to a requester. */
export type FormLoadFailure = "not_found" | "offline" | "error"

/**
 * Classify a failed locate or render request.
 *
 * A form the requester cannot read looks the same as one that does not exist,
 * so 403 and 404 share a state. An ID that is not a UUID fails validation
 * with 422 and is also "not found".
 */
export function getFormLoadFailure(error: unknown): FormLoadFailure {
  if (isFormOfflineError(error)) return "offline"
  const { status } = readApiError(error)
  return status === 403 || status === 404 || status === 422
    ? "not_found"
    : "error"
}

// Request validation paths start at the body: `["body", "config", ...]`.
function toConfigLoc(loc: unknown): Array<string | number> {
  const parts: unknown[] = Array.isArray(loc) ? loc : []
  if (!parts.every((p) => typeof p === "string" || typeof p === "number")) {
    return []
  }
  const path = parts[0] === "body" ? parts.slice(1) : parts
  return path[0] === "config" ? (path.slice(1) as Array<string | number>) : []
}

/**
 * Read the config problems out of a rejected save or validate request.
 *
 * The server rejects a config in two shapes: `form_config_invalid` with the
 * resolver's issues, and a request validation error whose entries carry a
 * path into the request body (a misspelt key, a bad tone, an oversized
 * image). Both come back as issues with a path into the config. Returns
 * `null` for any other failure.
 */
export function getFormConfigIssues(error: unknown): FormConfigIssue[] | null {
  const typed = getFormErrorDetail(error)
  if (typed) {
    return typed.code === "form_config_invalid" ? (typed.issues ?? []) : null
  }
  const { detail } = readApiError(error)
  if (!Array.isArray(detail)) return null
  const issues = detail.flatMap(
    (item: { msg?: unknown; loc?: unknown } | null): FormConfigIssue[] =>
      typeof item?.msg === "string"
        ? [{ severity: "error", message: item.msg, loc: toConfigLoc(item.loc) }]
        : []
  )
  return issues.length > 0 ? issues : null
}
