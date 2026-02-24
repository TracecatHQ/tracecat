"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  ApiError,
  InvitationRead,
  InvitationsCreateInvitationData,
  InvitationsListInvitationsData,
} from "@/client"
import {
  invitationsAcceptInvitation,
  invitationsCreateInvitation,
  invitationsGetInvitationByToken,
  invitationsListInvitations,
  invitationsRevokeInvitation,
} from "@/client"

/* ── Invitation list / create / revoke ──────────────────────────────── */

export function useInvitations(params?: InvitationsListInvitationsData) {
  const qc = useQueryClient()
  const workspaceId = params?.workspaceId

  const {
    data: invitations,
    isLoading: invitationsLoading,
    error: invitationsError,
  } = useQuery<InvitationRead[], ApiError>({
    queryKey: ["invitations", { workspaceId, status: params?.status }],
    queryFn: () => invitationsListInvitations(params ?? {}),
  })

  const { mutateAsync: createInvitation, isPending: createPending } =
    useMutation<
      InvitationRead | null,
      ApiError,
      InvitationsCreateInvitationData
    >({
      mutationFn: invitationsCreateInvitation,
      onSuccess: () => {
        qc.invalidateQueries({
          queryKey: ["invitations"],
        })
        if (workspaceId) {
          qc.invalidateQueries({
            queryKey: ["workspace", workspaceId, "members"],
          })
        }
        qc.invalidateQueries({ queryKey: ["org-members"] })
      },
    })

  const { mutateAsync: revokeInvitation, isPending: revokePending } =
    useMutation<unknown, ApiError, string>({
      mutationFn: (invitationId: string) =>
        invitationsRevokeInvitation({ invitationId }),
      onSuccess: () => {
        qc.invalidateQueries({
          queryKey: ["invitations"],
        })
        if (workspaceId) {
          qc.invalidateQueries({
            queryKey: ["workspace", workspaceId, "members"],
          })
        }
        qc.invalidateQueries({ queryKey: ["org-members"] })
      },
    })

  return {
    invitations,
    invitationsLoading,
    invitationsError,
    createInvitation,
    createPending,
    revokeInvitation,
    revokePending,
  }
}

/** @deprecated Use `useInvitations({ workspaceId })` instead. */
export function useWorkspaceInvitations(workspaceId: string) {
  return useInvitations({ workspaceId })
}

/* ── Token lookup (used by the unified accept page) ───────────────────── */

export function useInvitationByToken(token: string | null) {
  return useQuery({
    queryKey: ["invitation", token],
    queryFn: async () => {
      if (!token) {
        throw new Error("No invitation token provided")
      }
      return await invitationsGetInvitationByToken({ token })
    },
    enabled: !!token,
    retry: false,
  })
}

/* ── Accept mutation ──────────────────────────────────────────────────── */

export function useAcceptInvitation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (token: string) => {
      return await invitationsAcceptInvitation({
        requestBody: { token },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auth"] })
    },
  })
}
