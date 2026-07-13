"use client"

import { useQuery } from "@tanstack/react-query"
import {
  type OrganizationGetOrganizationResponse,
  type OrganizationListCurrentUserOrganizationMembershipsResponse,
  organizationGetOrganization,
  organizationListCurrentUserOrganizationMemberships,
} from "@/client"

/**
 * Hook to fetch the current organization.
 *
 * Returns basic information about the organization the authenticated user belongs to.
 */
export function useOrganization({
  enabled = true,
}: {
  enabled?: boolean
} = {}) {
  const {
    data: organization,
    isLoading,
    error,
  } = useQuery<OrganizationGetOrganizationResponse>({
    queryKey: ["current-organization"],
    queryFn: organizationGetOrganization,
    enabled,
    retry: false,
  })

  return {
    organization,
    isLoading,
    error,
  }
}

/**
 * Hook to fetch active organizations the current user belongs to.
 *
 * Used by the sidebar organization switcher to render only for multi-org users.
 */
export function useOrganizationMemberships({
  enabled = true,
}: {
  enabled?: boolean
} = {}) {
  const {
    data: organizations,
    isLoading,
    error,
  } = useQuery<OrganizationListCurrentUserOrganizationMembershipsResponse>({
    queryKey: ["organization-memberships"],
    queryFn: organizationListCurrentUserOrganizationMemberships,
    enabled,
    retry: false,
  })

  return {
    organizations,
    isLoading,
    error,
  }
}
