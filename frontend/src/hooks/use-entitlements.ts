import { useQuery } from "@tanstack/react-query"

import { organizationGetOrganizationEntitlements } from "@/client"
import { useOrganization } from "@/hooks/use-organization"

type EntitlementKey = keyof Awaited<
  ReturnType<typeof organizationGetOrganizationEntitlements>
>

export function useEntitlements(): {
  hasEntitlement: (key: EntitlementKey) => boolean
  isLoading: boolean
  hasEntitlementData: boolean
} {
  const { organization, isLoading: organizationLoading } = useOrganization()
  const {
    data: entitlements,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-entitlements", organization?.id],
    queryFn: async () => await organizationGetOrganizationEntitlements(),
    enabled: Boolean(organization?.id),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const hasEntitlementData = entitlements !== undefined

  return {
    hasEntitlement: (key: EntitlementKey) => {
      if (organizationLoading || isLoading || error) return false
      return Boolean(entitlements?.[key])
    },
    isLoading: organizationLoading || isLoading,
    hasEntitlementData,
  }
}
