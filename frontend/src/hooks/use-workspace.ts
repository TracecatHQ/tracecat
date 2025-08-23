"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
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

export function useCurrentUserRole() {
  const workspaceId = useWorkspaceId()
  const { user } = useAuth()
  return useQuery({
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
