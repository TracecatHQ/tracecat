import { useEntitlements } from "@/hooks/use-entitlements"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

/**
 * Whether the Forms feature is usable.
 *
 * - `loading`: the flag or the entitlement is not known yet, including after
 *   its request failed.
 * - `hidden`: the feature flag is off. Surfaces behave as if Forms did not exist.
 * - `locked`: the flag is on but the organization is not entitled.
 * - `enabled`: the flag is on and the organization is entitled.
 */
export type FormsAccess = "loading" | "hidden" | "locked" | "enabled"

/**
 * The one place the `forms` feature flag and entitlement are read; every
 * Forms surface gates on the result. The entitlement is not requested while
 * the flag is off. Mount this only for a signed-in user.
 */
export function useFormsAccess(): FormsAccess {
  const {
    isFeatureEnabled,
    isLoading: flagLoading,
    hasFeatureData,
  } = useFeatureFlag()
  const flagEnabled = isFeatureEnabled("forms")
  const {
    hasEntitlement,
    hasEntitlementData,
    isLoading: entitlementLoading,
  } = useEntitlements({
    enabled: flagEnabled,
  })
  // A failed request says nothing about the flag; don't 404 the page on it.
  if (flagLoading || !hasFeatureData) {
    return "loading"
  }
  if (!flagEnabled) {
    return "hidden"
  }
  // A failed request says nothing about the plan; don't show it as locked.
  if (entitlementLoading || !hasEntitlementData) {
    return "loading"
  }
  return hasEntitlement("forms") ? "enabled" : "locked"
}
