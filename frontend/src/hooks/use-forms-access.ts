import { useEntitlements } from "@/hooks/use-entitlements"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { type FormsAccess, resolveFormsAccess } from "@/lib/forms/access"

/**
 * Whether the Forms feature is usable by the current organization.
 *
 * This is the one place the `forms` feature flag and the `forms` entitlement
 * are read. Every Forms surface gates on the returned state:
 *
 * - `loading`: not known yet. Render nothing that depends on Forms.
 * - `hidden`: the flag is off. Behave as if Forms did not exist.
 * - `locked`: the flag is on but the organization is not entitled.
 * - `enabled`: Forms is available.
 *
 * The entitlement is not requested while the flag is off. A failed
 * entitlement request reads as `locked`.
 *
 * Both requests are authenticated, so mount this only for a signed-in user.
 */
export function useFormsAccess(): FormsAccess {
  const { isFeatureEnabled, isLoading: flagLoading } = useFeatureFlag()
  const flagEnabled = isFeatureEnabled("forms")
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements({
    enabled: flagEnabled,
  })
  return resolveFormsAccess({
    flagLoading,
    flagEnabled,
    entitlementLoading,
    entitled: hasEntitlement("forms"),
  })
}
