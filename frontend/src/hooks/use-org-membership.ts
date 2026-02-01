"use client"

import { useQuery } from "@tanstack/react-query"
import { type OrgMemberRead, organizationGetCurrentOrgMember } from "@/client"

/**
 * Hook to fetch the current user's organization membership.
 *
 * Returns the org membership details including the user's org role
 * (member, admin, or owner).
 */
export function useOrgMembership() {
  const {
    data: membership,
    isLoading,
    error,
  } = useQuery<OrgMemberRead>({
    queryKey: ["current-org-member"],
    queryFn: organizationGetCurrentOrgMember,
    retry: false, // Don't retry on 404 (user not in org)
  })

  // Check if user has org admin/owner privileges
  const isOrgAdmin =
    membership?.role === "admin" || membership?.role === "owner"

  return {
    membership,
    isLoading,
    error,
    isOrgAdmin,
  }
}
