"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type WorkspaceAddMemberResponse,
  type WorkspaceInvitationRead,
  type WorkspaceMember,
  type WorkspaceRead,
  type WorkspacesAddWorkspaceMemberData,
  type WorkspacesCreateWorkspaceInvitationData,
  type WorkspacesCreateWorkspaceMembershipData,
  type WorkspacesCreateWorkspaceMembershipResponse,
  workspacesAddWorkspaceMember,
  workspacesCreateWorkspaceInvitation,
  workspacesCreateWorkspaceMembership,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesListWorkspaceInvitations,
  workspacesListWorkspaceMembers,
  workspacesRevokeWorkspaceInvitation,
} from "@/client"
import { retryHandler } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

/* ── SELECTORS ─────────────────────────────────────────────────────────── */

export function useWorkspaceDetails() {
  const workspaceId = useWorkspaceId()
  const {
    data: workspace,
    isLoading: workspaceLoading,
    error: workspaceError,
  } = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: () => workspacesGetWorkspace({ workspaceId }),
    select: (d: WorkspaceRead | undefined) => d,
    enabled: !!workspaceId,
    retry: retryHandler,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return { workspace, workspaceLoading, workspaceError }
}

/* ── MUTATIONS ─────────────────────────────────────────────────────────── */

export function useWorkspaceMutations() {
  const workspaceId = useWorkspaceId()
  const qc = useQueryClient()

  const { mutateAsync: addMember, isPending: addPending } = useMutation<
    WorkspacesCreateWorkspaceMembershipResponse,
    Error,
    WorkspacesCreateWorkspaceMembershipData
  >({
    mutationFn: workspacesCreateWorkspaceMembership,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "members"],
        }),
      ])
    },
  })

  const { mutateAsync: removeMember, isPending: removePending } = useMutation<
    unknown,
    Error,
    string
  >({
    mutationFn: (userId: string) =>
      workspacesDeleteWorkspaceMembership({
        workspaceId,
        userId,
      }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
        qc.invalidateQueries({
          queryKey: ["workspace", workspaceId, "members"],
        }),
      ])
    },
  })

  return {
    addMember,
    addPending,
    removeMember,
    removePending,
  }
}

export function useWorkspaceMembers(
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const {
    data: members,
    isLoading: membersLoading,
    error: membersError,
  } = useQuery<WorkspaceMember[], ApiError>({
    queryKey: ["workspace", workspaceId, "members"],
    queryFn: () => workspacesListWorkspaceMembers({ workspaceId }),
    enabled: enabled && !!workspaceId,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return { members, membersLoading, membersError }
}

/* ── INVITATIONS ──────────────────────────────────────────────────────── */

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
        workspacesRevokeWorkspaceInvitation({ workspaceId, invitationId }),
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
