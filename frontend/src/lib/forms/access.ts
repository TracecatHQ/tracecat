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

/** Whether a set of workspace scopes lets its holder submit forms. */
export function canSubmitForms(scopes: Iterable<string>): boolean {
  return hasGrantedScope("form:submit", scopes)
}
