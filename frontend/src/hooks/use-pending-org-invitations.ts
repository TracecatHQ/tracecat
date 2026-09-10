"use client"

import {
  invitationsListMyPendingInvitations,
  type PendingInvitationRead,
} from "@/client"
import { useQuery } from "@/lib/query"

export function usePendingOrgInvitations() {
  const {
    data: pendingInvitations,
    isLoading: pendingInvitationsIsLoading,
    error: pendingInvitationsError,
  } = useQuery<PendingInvitationRead[]>({
    queryKey: ["pending-org-invitations"],
    queryFn: invitationsListMyPendingInvitations,
    retry: false,
  })

  return {
    pendingInvitations,
    pendingInvitationsIsLoading,
    pendingInvitationsError,
  }
}
