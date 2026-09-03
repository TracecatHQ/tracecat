"use client"

import { type OrgMemberDetail, organizationGetCurrentOrgMember } from "@/client"
import { useQuery } from "@/lib/query"

/**
 * Hook to fetch the current user's organization membership.
 *
 * For permission checks, use `useScopeCheck` from `@/components/auth/scope-guard` instead.
 */
export function useOrgMembership() {
  const {
    data: membership,
    isLoading,
    error,
  } = useQuery<OrgMemberDetail>({
    queryKey: ["current-org-member"],
    queryFn: organizationGetCurrentOrgMember,
    retry: false, // Don't retry on 404 (user not in org)
  })

  return {
    membership,
    isLoading,
    error,
  }
}
