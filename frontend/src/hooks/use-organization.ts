"use client"

import { useQuery } from "@tanstack/react-query"
import {
  type OrganizationGetOrganizationResponse,
  organizationGetOrganization,
} from "@/client"

/**
 * Hook to fetch the current organization.
 *
 * Returns basic information about the organization the authenticated user belongs to.
 */
export function useOrganization() {
  const {
    data: organization,
    isLoading,
    error,
  } = useQuery<OrganizationGetOrganizationResponse>({
    queryKey: ["current-organization"],
    queryFn: organizationGetOrganization,
    retry: false,
  })

  return {
    organization,
    isLoading,
    error,
  }
}
