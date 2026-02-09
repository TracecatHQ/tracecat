"use client"

import { useQuery } from "@tanstack/react-query"
import { organizationListOrganizationDomains } from "@/client"

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
