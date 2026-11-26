import { useEntitlements } from "@/hooks/use-entitlements"

/**
 * Whether the Triggers tab is usable.
 *
 * - `loading`: the entitlement is not known yet, including after its request
 *   failed.
 * - `locked`: the organization is not entitled.
 * - `enabled`: the organization is entitled.
 */
export type TriggersAccess = "loading" | "locked" | "enabled"

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
  // A failed request says nothing about the plan; don't show it as locked.
  if (isLoading || !hasEntitlementData) {
    return "loading"
  }
  return hasEntitlement("object_triggers") ? "enabled" : "locked"
}
