"use client"

import { useQuery } from "@tanstack/react-query"
import { type OrgMemberDetail, organizationGetCurrentOrgMember } from "@/client"
import { useAuth } from "@/hooks/use-auth"

/**
 * Hook to fetch the current user's organization membership and permissions.
 *
 * Returns:
 * - `membership`: The org membership details including org role
 * - `hasOrgAdminRole`: Whether user has org admin/owner role
 * - `canAdministerOrg`: Whether user can administer the org (platform admin OR org admin/owner)
 */
export function useOrgMembership() {
  const { user } = useAuth()
  const {
    data: membership,
    isLoading,
    error,
  } = useQuery<OrgMemberDetail>({
    queryKey: ["current-org-member"],
    queryFn: organizationGetCurrentOrgMember,
    retry: false, // Don't retry on 404 (user not in org)
  })

  // Check if user has org-level admin/owner role (not platform admin)
  const hasOrgAdminRole =
    membership?.role_slug === "admin" || membership?.role_slug === "owner"

  // Check if user can administer the org (platform admin OR org admin/owner)
  const canAdministerOrg = user?.isPlatformAdmin() || hasOrgAdminRole

  return {
    membership,
    isLoading,
    error,
    hasOrgAdminRole,
    canAdministerOrg,
  }
}
