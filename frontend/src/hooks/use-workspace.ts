"use client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type WorkspaceMember,
  type WorkspaceRead,
  type WorkspacesCreateWorkspaceMembershipData,
  type WorkspacesCreateWorkspaceMembershipResponse,
  workspacesCreateWorkspaceMembership,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesListWorkspaceMembers,
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
    enabled: Boolean(workspaceId) && enabled,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return { members, membersLoading, membersError }
}
