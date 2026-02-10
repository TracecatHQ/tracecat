"use client"

import { useQuery } from "@tanstack/react-query"
import {
  type OrgPendingInvitationRead,
  organizationListMyPendingInvitations,
} from "@/client"

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
