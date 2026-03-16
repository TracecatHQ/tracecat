"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

export type InvitationStatus = "pending" | "accepted" | "revoked" | "declined"

export type WorkspaceAssignmentInput = {
  workspace_id: string
  role_id: string
}

export type InvitationWorkspaceOptionRead = {
  invitation_id: string
  workspace_id: string
  workspace_name?: string | null
  role_id: string
  role_name: string
  role_slug?: string | null
  status: InvitationStatus
  expires_at: string
  created_at: string
  accepted_at?: string | null
}

export type InvitationRead = {
  id: string
  organization_id: string
  workspace_id?: string | null
  workspace_name?: string | null
  email: string
  role_id: string
  role_name: string
  role_slug?: string | null
  status: InvitationStatus
  invited_by?: string | null
  expires_at: string
  created_at: string
  accepted_at?: string | null
  token?: string | null
  workspace_options: InvitationWorkspaceOptionRead[]
}

export type InvitationReadMinimal = {
  invitation_id: string
  organization_id: string
  organization_slug: string
  organization_name: string
  workspace_id?: string | null
  workspace_name?: string | null
  inviter_name?: string | null
  inviter_email?: string | null
  role_name: string
  role_slug?: string | null
  status: InvitationStatus
  expires_at: string
  email_matches?: boolean | null
  accept_token: string
  workspace_options: InvitationWorkspaceOptionRead[]
}

export type InvitationCreateInput = {
  email: string
  role_id: string
  workspace_id?: string | null
  workspace_assignments?: WorkspaceAssignmentInput[] | null
}

export type InvitationCreateResponse = {
  message: string
  invitation: InvitationRead | null
}

type InvitationListParams = {
  workspaceId?: string
  status?: InvitationStatus
}

type InvitationAcceptInput = {
  token: string
  selectedWorkspaceIds?: string[]
}

class InvitationApiError extends Error {
  status: number
  detail?: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = "InvitationApiError"
    this.status = status
    this.detail = detail
  }
}

function getErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) {
    return detail
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message
    if (typeof message === "string" && message) {
      return message
    }
  }
  return fallback
}

async function invitationRequest<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
    ...init,
  })

  const text = await response.text()
  const data = text ? (JSON.parse(text) as unknown) : undefined

  if (!response.ok) {
    const detail =
      data && typeof data === "object"
        ? (data as { detail?: unknown }).detail
        : data
    throw new InvitationApiError(
      getErrorMessage(detail, `Request failed with status ${response.status}`),
      response.status,
      detail
    )
  }

  return data as T
}

function createInvitationQuery(params?: InvitationListParams): string {
  const query = new URLSearchParams()
  if (params?.workspaceId) {
    query.set("workspace_id", params.workspaceId)
  }
  if (params?.status) {
    query.set("status", params.status)
  }
  const search = query.toString()
  return search ? `?${search}` : ""
}

async function listInvitations(
  params?: InvitationListParams
): Promise<InvitationRead[]> {
  return invitationRequest<InvitationRead[]>(
    `/invitations${createInvitationQuery(params)}`
  )
}

async function createInvitation(
  requestBody: InvitationCreateInput
): Promise<InvitationCreateResponse> {
  return invitationRequest<InvitationCreateResponse>("/invitations", {
    method: "POST",
    body: JSON.stringify(requestBody),
  })
}

async function revokeInvitation(invitationId: string): Promise<void> {
  await invitationRequest<void>(`/invitations/${invitationId}`, {
    method: "DELETE",
  })
}

export async function getInvitationToken(
  invitationId: string
): Promise<string> {
  const response = await invitationRequest<{ token: string }>(
    `/invitations/${invitationId}/token`
  )
  return response.token
}

export function buildInvitationAcceptUrl(token: string): string {
  return `/invitations/accept?token=${encodeURIComponent(token)}`
}

function invalidateInvitationQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId?: string
) {
  queryClient.invalidateQueries({ queryKey: ["invitations"] })
  queryClient.invalidateQueries({ queryKey: ["org-members"] })
  if (workspaceId) {
    queryClient.invalidateQueries({
      queryKey: ["workspace", workspaceId, "members"],
    })
  }
}

export function useInvitations(params?: InvitationListParams) {
  const queryClient = useQueryClient()
  const workspaceId = params?.workspaceId

  const {
    data: invitations,
    isLoading: invitationsLoading,
    error: invitationsError,
  } = useQuery<InvitationRead[], InvitationApiError>({
    queryKey: ["invitations", { workspaceId, status: params?.status }],
    queryFn: () => listInvitations(params),
  })

  const { mutateAsync: createInvitationMutate, isPending: createPending } =
    useMutation<
      InvitationCreateResponse,
      InvitationApiError,
      InvitationCreateInput
    >({
      mutationFn: createInvitation,
      onSuccess: () => {
        invalidateInvitationQueries(queryClient, workspaceId)
      },
    })

  const { mutateAsync: revokeInvitationMutate, isPending: revokePending } =
    useMutation<void, InvitationApiError, string>({
      mutationFn: revokeInvitation,
      onSuccess: () => {
        invalidateInvitationQueries(queryClient, workspaceId)
      },
    })

  return {
    invitations,
    invitationsLoading,
    invitationsError,
    createInvitation: createInvitationMutate,
    createPending,
    revokeInvitation: revokeInvitationMutate,
    revokePending,
  }
}

export function useInvitationByToken(token: string | null) {
  return useQuery<InvitationReadMinimal, InvitationApiError>({
    queryKey: ["invitation", token],
    queryFn: async () => {
      if (!token) {
        throw new Error("No invitation token provided")
      }
      return invitationRequest<InvitationReadMinimal>(
        `/invitations/token/${encodeURIComponent(token)}`
      )
    },
    enabled: !!token,
    retry: false,
  })
}

export function useAcceptInvitation() {
  const queryClient = useQueryClient()

  return useMutation<unknown, InvitationApiError, InvitationAcceptInput>({
    mutationFn: async ({ token, selectedWorkspaceIds }) =>
      invitationRequest("/invitations/accept", {
        method: "POST",
        body: JSON.stringify({
          token,
          ...(selectedWorkspaceIds
            ? { selected_workspace_ids: selectedWorkspaceIds }
            : {}),
        }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auth"] })
      queryClient.invalidateQueries({ queryKey: ["invitation"] })
    },
  })
}

export function useDeclineInvitation() {
  const queryClient = useQueryClient()

  return useMutation<unknown, InvitationApiError, { token: string }>({
    mutationFn: async ({ token }) =>
      invitationRequest("/invitations/decline", {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invitation"] })
    },
  })
}
