"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type WorkspaceMember,
  type WorkspaceRead,
  type WorkspacesCreateWorkspaceMembershipData,
  type WorkspacesCreateWorkspaceMembershipResponse,
  type WorkspacesUpdateWorkspaceMembershipData,
  type WorkspacesUpdateWorkspaceMembershipResponse,
  workspacesCreateWorkspaceMembership,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesListWorkspaceMembers,
  workspacesUpdateWorkspaceMembership,
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
