"use client"

import { useQuery } from "@tanstack/react-query"

export type PendingInvitationRead = {
  accept_token: string
  organization_id: string
  organization_name: string
  workspace_id?: string | null
  workspace_name?: string | null
  inviter_name?: string | null
  inviter_email?: string | null
  role_name: string
  role_slug?: string | null
  expires_at: string
  workspace_options: Array<{
    invitation_id: string
    workspace_id: string
    workspace_name?: string | null
    role_id: string
    role_name: string
    role_slug?: string | null
    status: "pending" | "accepted" | "revoked" | "declined"
    expires_at: string
    created_at: string
    accepted_at?: string | null
  }>
}

async function listPendingInvitations(): Promise<PendingInvitationRead[]> {
  const response = await fetch("/api/invitations/pending/me", {
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  })

  const text = await response.text()
  const data = text ? (JSON.parse(text) as unknown) : undefined

  if (!response.ok) {
    const detail =
      data && typeof data === "object"
        ? (data as { detail?: unknown }).detail
        : data
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Request failed with status ${response.status}`
    )
  }

  return data as PendingInvitationRead[]
}

export function usePendingOrgInvitations() {
  const {
    data: pendingInvitations,
    isLoading: pendingInvitationsIsLoading,
    error: pendingInvitationsError,
  } = useQuery<PendingInvitationRead[]>({
    queryKey: ["pending-org-invitations"],
    queryFn: listPendingInvitations,
    retry: false,
  })

  return {
    pendingInvitations,
    pendingInvitationsIsLoading,
    pendingInvitationsError,
  }
}
