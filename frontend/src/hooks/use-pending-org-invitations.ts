"use client"

import { useQuery } from "@tanstack/react-query"
import {
  invitationsListMyPendingInvitations,
  type PendingInvitationRead,
} from "@/client"

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
