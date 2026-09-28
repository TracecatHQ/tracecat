"use client"

import {
  type OrgPendingInvitationRead,
  organizationListMyPendingInvitations,
} from "@/client"
import { useQuery } from "@/lib/query"

export function usePendingOrgInvitations() {
  const {
    data: pendingInvitations,
    isLoading: pendingInvitationsIsLoading,
    error: pendingInvitationsError,
  } = useQuery<OrgPendingInvitationRead[]>({
    queryKey: ["pending-org-invitations"],
    queryFn: organizationListMyPendingInvitations,
    retry: false,
  })

  return {
    pendingInvitations,
    pendingInvitationsIsLoading,
    pendingInvitationsError,
  }
}
