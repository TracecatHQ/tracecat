"use client"
import {
  type ApiError,
  type InvitationCreate,
  invitationsCreateInvitation,
  type WorkspaceAssignableRole,
  type WorkspaceMember,
  type WorkspaceRead,
  type WorkspacesCreateWorkspaceMembershipData,
  type WorkspacesCreateWorkspaceMembershipResponse,
  workspacesCreateWorkspaceMembership,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesListWorkspaceAssignableRoles,
  workspacesListWorkspaceMembers,
  workspacesUpdateWorkspaceMembership,
} from "@/client"
import { retryHandler } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"
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

  const invalidateMembers = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
      qc.invalidateQueries({
        queryKey: ["workspace", workspaceId, "members"],
      }),
    ])
  }

  // Callers render add/invite failures inline, so skip the global error toast.
  const { mutateAsync: addMember, isPending: addPending } = useMutation<
    WorkspacesCreateWorkspaceMembershipResponse,
    Error,
    WorkspacesCreateWorkspaceMembershipData
  >({
    mutationFn: workspacesCreateWorkspaceMembership,
    onSuccess: invalidateMembers,
    meta: { suppressErrorToast: true },
  })

  const { mutateAsync: inviteMember, isPending: invitePending } = useMutation({
    mutationFn: (params: InvitationCreate) =>
      invitationsCreateInvitation({ requestBody: params }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["org-members"] }),
        qc.invalidateQueries({ queryKey: ["org-invitations", "pending"] }),
      ])
    },
    meta: { suppressErrorToast: true },
  })

  const { mutateAsync: updateMemberRole, isPending: updateRolePending } =
    useMutation<unknown, Error, { userId: string; roleId: string }>({
      mutationFn: ({ userId, roleId }) =>
        workspacesUpdateWorkspaceMembership({
          workspaceId,
          userId,
          requestBody: { role_id: roleId },
        }),
      onSuccess: invalidateMembers,
      meta: { suppressErrorToast: true },
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
    onSuccess: invalidateMembers,
  })

  return {
    addMember,
    addPending,
    inviteMember,
    invitePending,
    updateMemberRole,
    updateRolePending,
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

/** Roles the caller may grant on this workspace, computed by the server. */
export function useWorkspaceAssignableRoles(
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const {
    data: assignableRoles,
    isLoading: assignableRolesLoading,
    error: assignableRolesError,
  } = useQuery<WorkspaceAssignableRole[], ApiError>({
    queryKey: ["workspace", workspaceId, "assignable-roles"],
    queryFn: () => workspacesListWorkspaceAssignableRoles({ workspaceId }),
    enabled: enabled && !!workspaceId,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return { assignableRoles, assignableRolesLoading, assignableRolesError }
}
