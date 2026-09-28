import { organizationGetOrganizationEntitlements } from "@/client"
import { useOrganization } from "@/hooks/use-organization"
import { useQuery } from "@/lib/query"

/** Key of a single organization entitlement, as returned by the API. */
export type EntitlementKey = keyof Awaited<
  ReturnType<typeof organizationGetOrganizationEntitlements>
>

export function useEntitlements({
  enabled = true,
}: {
  enabled?: boolean
} = {}): {
  hasEntitlement: (key: EntitlementKey) => boolean
  isLoading: boolean
  hasEntitlementData: boolean
} {
  const { organization, isLoading: organizationLoading } = useOrganization({
    enabled,
  })
  const {
    data: entitlements,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-entitlements", organization?.id],
    queryFn: async () => await organizationGetOrganizationEntitlements(),
    enabled: enabled && Boolean(organization?.id),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  // React Query keeps the last good `entitlements` when a refetch fails, while
  // `hasEntitlement` below starts answering false. Reporting "known" off stale
  // data and "not entitled" off the error would tell a paying org it lacks a
  // feature; callers that separate those states must see "unknown" instead, so
  // both halves distrust an errored result.
  const hasEntitlementData = entitlements !== undefined && !error

  return {
    hasEntitlement: (key: EntitlementKey) => {
      if (!enabled) return false
      if (organizationLoading || isLoading || error) return false
      return Boolean(entitlements?.[key])
    },
    isLoading: enabled ? organizationLoading || isLoading : false,
    hasEntitlementData,
  }
}
