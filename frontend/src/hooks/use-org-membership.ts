"use client"

import { useQuery } from "@tanstack/react-query"
import { type OrgMemberRead, organizationGetCurrentOrgMember } from "@/client"

/**
 * Hook to fetch the current user's organization membership.
 *
 * Returns the org membership details including the user's org role
 * (member, admin, or owner).
 *
 * Use `hasOrgAdminRole` to check if the user has org admin/owner privileges.
 * Combine with `user.isPlatformAdmin()` for full org administration check:
 *
 * ```ts
 * const canAdministerOrg = user?.isPlatformAdmin() || hasOrgAdminRole
 * ```
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

  // Check if user has org-level admin/owner role (not platform admin)
  const hasOrgAdminRole =
    membership?.role === "admin" || membership?.role === "owner"

  return {
    membership,
    isLoading,
    error,
    hasOrgAdminRole,
  }
}
