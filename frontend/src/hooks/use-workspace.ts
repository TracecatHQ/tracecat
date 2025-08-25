"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type WorkspaceMember,
  type WorkspaceMembershipRead,
  type WorkspaceRead,
  type WorkspacesCreateWorkspaceMembershipData,
  type WorkspacesCreateWorkspaceMembershipResponse,
  type WorkspacesUpdateWorkspaceMembershipData,
  type WorkspacesUpdateWorkspaceMembershipResponse,
  workspacesCreateWorkspaceMembership,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesGetWorkspaceMembership,
  workspacesListWorkspaceMembers,
  workspacesUpdateWorkspaceMembership,
} from "@/client"
import { useAuth } from "@/hooks/use-auth"
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
  })

  return { workspace, workspaceLoading, workspaceError }
}

/**
 * Returns the membership role of the current user in the specified workspace.
 *
 * @param workspaceId - The ID of the workspace to check membership for.
 * @returns An object containing:
 *   - role: The user's role in the workspace, or undefined if not found.
 *   - roleLoading: Whether the role is currently loading.
 *   - roleError: Any error encountered while fetching the role.
 */
export function useCurrentUserRole(workspaceId: string) {
  const { user } = useAuth()
  const {
    data: role,
    isLoading: roleLoading,
    error: roleError,
  } = useQuery({
    queryKey: ["membership", workspaceId, user?.id],
    queryFn: () =>
      workspacesGetWorkspaceMembership({
        workspaceId,
        userId: user!.id,
      }),
    select: (m: WorkspaceMembershipRead | undefined) => m?.role,
    enabled: !!user?.id,
    retry: retryHandler,
    staleTime: 300_000,
  })
  return { role, roleLoading, roleError }
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
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
  })

  const { mutateAsync: updateMember, isPending: updatePending } = useMutation<
    WorkspacesUpdateWorkspaceMembershipResponse,
    Error,
    WorkspacesUpdateWorkspaceMembershipData
  >({
    mutationFn: workspacesUpdateWorkspaceMembership,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", workspaceId] })
      qc.invalidateQueries({ queryKey: ["membership", workspaceId] })
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
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
  })

  return {
    addMember,
    addPending,
    updateMember,
    updatePending,
    removeMember,
    removePending,
  }
}

export function useWorkspaceMembers(workspaceId: string) {
  const {
    data: members,
    isLoading: membersLoading,
    error: membersError,
  } = useQuery<WorkspaceMember[], ApiError>({
    queryKey: ["workspace", workspaceId, "members"],
    queryFn: () => workspacesListWorkspaceMembers({ workspaceId }),
  })

  return { members, membersLoading, membersError }
}
