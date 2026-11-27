import { getApiErrorDetail, getErrorStatus } from "@/lib/errors"

// Plain words for the scopes these pages can be refused on.
const SCOPE_ACTIONS: Record<string, string> = {
  "trigger:create": "create triggers",
  "trigger:update": "change triggers",
  "trigger:delete": "delete triggers",
  "schedule:create": "create schedules",
  "schedule:update": "change schedules",
  "schedule:delete": "delete schedules",
  "workflow:execute": "run workflows",
  "agent:execute": "run agents",
}

/** Missing scopes named by a 403 `insufficient_scope` body, if any. */
function getMissingScopes(error: unknown): string[] {
  if (typeof error !== "object" || error === null || !("body" in error)) {
    return []
  }
  const body = error.body
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return []
  }
  const apiError = body.error
  if (
    typeof apiError !== "object" ||
    apiError === null ||
    !("missing_scopes" in apiError) ||
    !Array.isArray(apiError.missing_scopes)
  ) {
    return []
  }
  return apiError.missing_scopes.filter(
    (scope): scope is string => typeof scope === "string"
  )
}

/**
 * A user-facing message for a failed trigger or event source request.
 *
 * A 403 names the missing permission in plain words, because the shared error
 * pipeline swallows 403s and a target needs its own execute scope on top of
 * `trigger:update`.
 */
export function describeTriggerError(error: unknown): string {
  if (getErrorStatus(error) === 403) {
    const scopes = getMissingScopes(error)
    if (scopes.length === 0) {
      return "You don't have permission to do this in this workspace."
    }
    const actions = scopes.map((scope) => SCOPE_ACTIONS[scope] ?? scope)
    return `You don't have permission to ${actions.join(" or ")} (${scopes.join(", ")}). Ask a workspace admin for access.`
  }
  return getApiErrorDetail(error) ?? "Something went wrong. Please try again."
}
