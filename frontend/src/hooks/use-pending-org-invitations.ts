"use client"

import { useQuery } from "@tanstack/react-query"
import type { PendingInvitationRead } from "@/client"

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
