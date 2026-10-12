import type { FormErrorCode, FormErrorDetail, FormFieldError } from "@/client"
import type { FormConfigIssue } from "@/lib/forms/yaml-diagnostics"

interface ApiErrorLike {
  status?: unknown
  body?: { detail?: unknown } | null
}

function asApiError(error: unknown): ApiErrorLike | null {
  return typeof error === "object" && error !== null
    ? (error as ApiErrorLike)
    : null
}

/** Read the typed `detail` of a failed forms request, if it carries one. */
export function getFormErrorDetail(error: unknown): FormErrorDetail | null {
  const detail = asApiError(error)?.body?.detail
  if (
    typeof detail === "object" &&
    detail !== null &&
    "code" in detail &&
    typeof detail.code === "string" &&
    "message" in detail &&
    typeof detail.message === "string"
  ) {
    return detail as FormErrorDetail
  }
  return null
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
  if (isFormOfflineError(error)) {
    return "offline"
  }
  const status = asApiError(error)?.status
  if (status === 403 || status === 404 || status === 422) {
    return "not_found"
  }
  return "error"
}

/** Server errors of a rejected submission, split by where they are shown. */
export interface MappedFormErrors {
  /** One message per field on screen, keyed by field name. */
  fieldErrors: Record<string, string>
  /** Messages for errors that belong to no field on screen. */
  formErrors: string[]
}

/**
 * Split a rejected submission's errors into per-field and form-level ones.
 *
 * The server can report several errors for one field (a required `file`
 * field can report both `required` and `invalid_file`). Later errors
 * come from later, more specific checks, so the last one wins.
 */
export function mapFormFieldErrors(
  errors: FormFieldError[] | null | undefined,
  visibleFieldNames: Iterable<string>
): MappedFormErrors {
  const visible = new Set(visibleFieldNames)
  const mapped: MappedFormErrors = { fieldErrors: {}, formErrors: [] }
  for (const error of errors ?? []) {
    if (visible.has(error.field)) {
      mapped.fieldErrors[error.field] = error.message
    } else {
      mapped.formErrors.push(`${error.field}: ${error.message}`)
    }
  }
  return mapped
}

function isLoc(value: unknown): value is Array<string | number> {
  return (
    Array.isArray(value) &&
    value.every((part) => typeof part === "string" || typeof part === "number")
  )
}

// Request validation paths start at the body: `["body", "config", ...]`.
function toConfigLoc(loc: Array<string | number>): Array<string | number> {
  const path = loc[0] === "body" ? loc.slice(1) : loc
  return path[0] === "config" ? path.slice(1) : []
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
  const detail = asApiError(error)?.body?.detail
  if (!Array.isArray(detail)) {
    return null
  }
  const issues: FormConfigIssue[] = []
  for (const item of detail as unknown[]) {
    if (typeof item !== "object" || item === null) {
      continue
    }
    const { msg, loc } = item as { msg?: unknown; loc?: unknown }
    if (typeof msg === "string") {
      issues.push({
        severity: "error",
        message: msg,
        loc: isLoc(loc) ? toConfigLoc(loc) : [],
      })
    }
  }
  return issues.length > 0 ? issues : null
}
