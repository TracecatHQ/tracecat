import { type EntitlementKey, useEntitlements } from "@/hooks/use-entitlements"

/**
 * Whether the organization can use a trigger kind.
 *
 * - `loading`: the entitlement request is in flight.
 * - `unknown`: the entitlement request failed; the plan is not known.
 * - `locked`: the organization is not entitled.
 * - `enabled`: the kind needs no feature, or the organization has it.
 */
export type TriggerKindAccess = "loading" | "unknown" | "locked" | "enabled"

/**
 * Reads the plan for the Triggers tab's kind picker. The tab itself is open to
 * every plan; each kind names the feature it needs (`entitlement` in
 * `TRIGGER_KINDS`). Mount this only for a signed-in user.
 */
export function useTriggerKindAccess(): (
  entitlement: EntitlementKey | null
) => TriggerKindAccess {
  const { hasEntitlement, hasEntitlementData, isLoading } = useEntitlements()
  return (entitlement) => {
    if (entitlement === null) {
      return "enabled"
    }
    if (isLoading) {
      return "loading"
    }
    // A failed request says nothing about the plan; don't show it as locked.
    if (!hasEntitlementData) {
      return "unknown"
    }
    return hasEntitlement(entitlement) ? "enabled" : "locked"
  }
}
