import { useEntitlements } from "@/hooks/use-entitlements"

/**
 * Whether the Triggers tab is usable.
 *
 * - `loading`: the entitlement request is in flight.
 * - `unknown`: the entitlement request failed; the plan is not known.
 * - `locked`: the organization is not entitled.
 * - `enabled`: the organization is entitled.
 */
export type TriggersAccess = "loading" | "unknown" | "locked" | "enabled"

/**
 * Reads the `object_triggers` entitlement for the Triggers pages, which gate
 * on the result. Mount this only for a signed-in user.
 */
export function useTriggersAccess({
  enabled = true,
}: {
  enabled?: boolean
} = {}): TriggersAccess {
  const { hasEntitlement, hasEntitlementData, isLoading } = useEntitlements({
    enabled,
  })
  if (isLoading) {
    return "loading"
  }
  // A failed request says nothing about the plan; don't show it as locked.
  if (!hasEntitlementData) {
    return "unknown"
  }
  return hasEntitlement("object_triggers") ? "enabled" : "locked"
}
