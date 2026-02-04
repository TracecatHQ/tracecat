import { useQuery } from "@tanstack/react-query"

import { organizationGetOrganizationEntitlements } from "@/client"

type EntitlementKey = keyof Awaited<
  ReturnType<typeof organizationGetOrganizationEntitlements>
>

export function useEntitlements(): {
  hasEntitlement: (key: EntitlementKey) => boolean
  isLoading: boolean
  hasEntitlementData: boolean
} {
  const {
    data: entitlements,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-entitlements"],
    queryFn: async () => await organizationGetOrganizationEntitlements(),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const hasEntitlementData = entitlements !== undefined

  return {
    hasEntitlement: (key: EntitlementKey) => {
      if (isLoading || error) return false
      return Boolean(entitlements?.[key])
    },
    isLoading,
    hasEntitlementData,
  }
}
