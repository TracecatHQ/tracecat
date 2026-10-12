import { hasGrantedScope } from "@/lib/scopes"

/**
 * Whether the Forms feature is usable.
 *
 * - `loading`: the flag or the entitlement is not known yet.
 * - `hidden`: the feature flag is off. Surfaces behave as if Forms did not exist.
 * - `locked`: the flag is on but the organization is not entitled.
 * - `enabled`: the flag is on and the organization is entitled.
 */
export type FormsAccess = "loading" | "hidden" | "locked" | "enabled"

/** Inputs of {@link resolveFormsAccess}. */
export interface FormsAccessInput {
  flagLoading: boolean
  flagEnabled: boolean
  entitlementLoading: boolean
  entitled: boolean
}

/** Combine the `forms` feature flag and entitlement into one state. */
export function resolveFormsAccess({
  flagLoading,
  flagEnabled,
  entitlementLoading,
  entitled,
}: FormsAccessInput): FormsAccess {
  if (flagLoading) {
    return "loading"
  }
  if (!flagEnabled) {
    return "hidden"
  }
  if (entitlementLoading) {
    return "loading"
  }
  return entitled ? "enabled" : "locked"
}

const FORM_DEEP_LINK = /^\/workspaces\/[^/]+\/forms\/([^/?#]+)/

/** Inputs of {@link getIntakeRedirect}. `undefined` means still loading. */
export interface IntakeRedirectInput {
  /** Whether the user holds `workspace:read` in the workspace. */
  canReadWorkspace: boolean | undefined
  /** Whether the user holds `form:read` in the workspace. */
  canReadForms: boolean | undefined
  formsAccess: FormsAccess
  pathname: string | null | undefined
}

/**
 * Where to send an intake-only user who opened a workspace page, or `null`
 * to leave them where they are.
 *
 * Intake-only means no `workspace:read` but `form:read`: the workspace shell
 * is closed to them and the requester pages under `/forms` are theirs. A
 * link to a form's builder page maps to that form's requester page. Nothing
 * happens while a scope or the feature flag is loading, and nothing happens
 * with the flag off. The rule reads these two scopes only, so it holds
 * whatever other scopes a member's role carries.
 */
export function getIntakeRedirect({
  canReadWorkspace,
  canReadForms,
  formsAccess,
  pathname,
}: IntakeRedirectInput): string | null {
  if (canReadWorkspace !== false || canReadForms !== true) {
    return null
  }
  if (formsAccess === "loading" || formsAccess === "hidden") {
    return null
  }
  const formId = pathname?.match(FORM_DEEP_LINK)?.[1]
  return formId ? `/forms/${formId}` : "/forms"
}

/** Whether a set of workspace scopes lets its holder submit forms. */
export function canSubmitForms(scopes: Iterable<string>): boolean {
  return hasGrantedScope("form:submit", scopes)
}
