"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  ApiError,
  WorkspaceInvitationRead,
  WorkspacesCreateWorkspaceInvitationData,
} from "@/client"
import {
  invitationsAcceptInvitation,
  invitationsGetInvitationByToken,
  invitationsRevokeInvitation,
  workspacesCreateWorkspaceInvitation,
  workspacesListWorkspaceInvitations,
} from "@/client"

/* ── Workspace invitation list / create / revoke ──────────────────────── */

export function useWorkspaceInvitations(workspaceId: string) {
  const qc = useQueryClient()

  const {
    data: invitations,
    isLoading: invitationsLoading,
    error: invitationsError,
  } = useQuery<WorkspaceInvitationRead[], ApiError>({
    queryKey: ["workspace", workspaceId, "invitations"],
    queryFn: () => workspacesListWorkspaceInvitations({ workspaceId }),
  })

  const { mutateAsync: createInvitation, isPending: createPending } =
    useMutation<
      WorkspaceInvitationRead,
      ApiError,
      WorkspacesCreateWorkspaceInvitationData
    >({
      mutationFn: workspacesCreateWorkspaceInvitation,
      onSuccess: () => {
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "invitations"],
        })
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "members"],
        })
      },
    })

  const { mutateAsync: revokeInvitation, isPending: revokePending } =
    useMutation<unknown, ApiError, string>({
      mutationFn: (invitationId: string) =>
        invitationsRevokeInvitation({ invitationId }),
      onSuccess: () => {
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "invitations"],
        })
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "members"],
        })
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
