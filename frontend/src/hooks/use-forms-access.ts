import { useEntitlements } from "@/hooks/use-entitlements"

/**
 * Whether the Forms feature is usable.
 *
 * - `loading`: the entitlement is not known yet, including after its request
 *   failed.
 * - `locked`: the organization is not entitled.
 * - `enabled`: the organization is entitled.
 */
export type FormsAccess = "loading" | "locked" | "enabled"

/**
 * The one place the `forms` entitlement is read; every Forms surface gates on
 * the result. Mount this only for a signed-in user.
 */
export function useFormsAccess(): FormsAccess {
  const { hasEntitlement, hasEntitlementData, isLoading } = useEntitlements()
  // A failed request says nothing about the plan; don't show it as locked.
  if (isLoading || !hasEntitlementData) {
    return "loading"
  }
  return hasEntitlement("forms") ? "enabled" : "locked"
}
