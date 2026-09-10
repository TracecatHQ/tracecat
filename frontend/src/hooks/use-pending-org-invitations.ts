"use client"

import {
  organizationListMyPendingInvitations,
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
    queryFn: organizationListMyPendingInvitations,
    retry: false,
  })

  return {
    pendingInvitations,
    pendingInvitationsIsLoading,
    pendingInvitationsError,
  }
}
