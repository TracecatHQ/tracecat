"use client"

import { organizationListOrganizationDomains } from "@/client"
import { useQuery } from "@/lib/query"

export function useOrgDomains() {
  const {
    data: domains,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["organization-domains"],
    queryFn: organizationListOrganizationDomains,
    retry: false,
  })

  return {
    domains,
    isLoading,
    error,
  }
}
