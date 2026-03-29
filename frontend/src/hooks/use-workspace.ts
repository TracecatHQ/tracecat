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

type WorkspaceMembershipBulkCreate = {
  user_ids: string[]
  role_id: string
}

type WorkspaceMembershipBulkCreateResponse = {
  processed_count: number
}

function getWorkspaceMutationErrorMessage(
  detail: unknown,
  fallback: string
): string {
  if (typeof detail === "string" && detail) {
    return detail
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message
    if (typeof message === "string" && message) {
      return message
    }
    const nestedDetail = (detail as { detail?: unknown }).detail
    if (typeof nestedDetail === "string" && nestedDetail) {
      return nestedDetail
    }
  }
  return fallback
}

async function createWorkspaceMembershipsBulk(
  workspaceId: string,
  requestBody: WorkspaceMembershipBulkCreate
): Promise<WorkspaceMembershipBulkCreateResponse> {
  const response = await fetch(
    `/api/workspaces/${workspaceId}/memberships/bulk`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestBody),
    }
  )

  const text = await response.text()
  const data = text ? (JSON.parse(text) as unknown) : undefined

  if (!response.ok) {
    const detail =
      data && typeof data === "object"
        ? (data as { detail?: unknown }).detail
        : data
    throw new Error(
      getWorkspaceMutationErrorMessage(
        detail,
        `Request failed with status ${response.status}`
      )
    )
  }

  return data as WorkspaceMembershipBulkCreateResponse
}

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

  const { mutateAsync: addMembersBulk, isPending: addMembersBulkPending } =
    useMutation<
      WorkspaceMembershipBulkCreateResponse,
      Error,
      WorkspaceMembershipBulkCreate
    >({
      mutationFn: (requestBody) =>
        createWorkspaceMembershipsBulk(workspaceId, requestBody),
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
    addMembersBulk,
    addMembersBulkPending,
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
