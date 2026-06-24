import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type OrgInvitationBatchCreate,
  organizationCreateInvitationsBulk,
  organizationResendInvitation,
  organizationRevokeInvitation,
  type WorkspaceInvitationBatchCreate,
  type WorkspaceInvitationRead,
  workspacesCreateWorkspaceInvitationsBulk,
  workspacesListWorkspaceInvitations,
  workspacesResendWorkspaceInvitation,
  workspacesRevokeWorkspaceInvitation,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"

/**
 * Organization-level invitation mutations.
 *
 * Shares the `["org-members"]` query cache with {@link useOrgMembers} so that
 * creating or revoking an invitation refreshes the members table. Member CRUD
 * itself lives in `useOrgMembers`; this hook only owns invitation actions.
 */
export function useOrgInvitations() {
  const queryClient = useQueryClient()
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["org-members"] })

  const {
    mutateAsync: createInvitationsBulk,
    isPending: createInvitationsBulkIsPending,
  } = useMutation({
    mutationFn: async (params: OrgInvitationBatchCreate) =>
      await organizationCreateInvitationsBulk({ requestBody: params }),
    onSuccess: invalidate,
    onError: (error: TracecatApiError) => {
      const detail = error.body?.detail
      toast({
        title: "Failed to create invitations",
        description: typeof detail === "string" ? detail : error.message,
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: resendInvitation } = useMutation({
    mutationFn: async (invitationId: string) =>
      await organizationResendInvitation({ invitationId }),
    onSuccess: () => {
      toast({
        title: "Invitation resent",
        description: "The invitation email has been resent.",
      })
    },
    onError: (error: TracecatApiError) => {
      const detail = error.body?.detail
      toast({
        title: "Failed to resend invitation",
        description: typeof detail === "string" ? detail : error.message,
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: revokeInvitation } = useMutation({
    mutationFn: async (invitationId: string) =>
      await organizationRevokeInvitation({ invitationId }),
    onSuccess: () => {
      invalidate()
      toast({
        title: "Invitation revoked",
        description: "Invitation has been revoked.",
      })
    },
    onError: (error: TracecatApiError) => {
      const detail = error.body?.detail
      toast({
        title: "Failed to revoke invitation",
        description: typeof detail === "string" ? detail : error.message,
        variant: "destructive",
      })
    },
  })

  return {
    createInvitationsBulk,
    createInvitationsBulkIsPending,
    resendInvitation,
    revokeInvitation,
  }
}

/**
 * Workspace-level invitations: list plus bulk create, resend, and revoke.
 *
 * Self-contained against the `["workspace", workspaceId, "invitations"]`
 * query cache.
 *
 * @param workspaceId - The workspace to scope invitations to.
 * @param options.listEnabled - Whether to run the list query. Mutation-only
 *   callers (e.g. the invite dialog) should pass `false` so they do not trigger
 *   an unnecessary list fetch. Defaults to `true`.
 */
export function useWorkspaceInvitations(
  workspaceId: string,
  options?: { listEnabled?: boolean }
) {
  const listEnabled = options?.listEnabled ?? true
  const queryClient = useQueryClient()
  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["workspace", workspaceId, "invitations"],
    })

  const {
    data: invitations,
    isLoading: invitationsLoading,
    error: invitationsError,
  } = useQuery<WorkspaceInvitationRead[]>({
    queryKey: ["workspace", workspaceId, "invitations"],
    queryFn: async () =>
      await workspacesListWorkspaceInvitations({ workspaceId }),
    enabled: listEnabled && !!workspaceId,
  })

  const {
    mutateAsync: createInvitationsBulk,
    isPending: createInvitationsBulkIsPending,
  } = useMutation({
    mutationFn: async (params: WorkspaceInvitationBatchCreate) =>
      await workspacesCreateWorkspaceInvitationsBulk({
        workspaceId,
        requestBody: params,
      }),
    onSuccess: invalidate,
    onError: (error: TracecatApiError) => {
      const detail = error.body?.detail
      toast({
        title: "Failed to create invitations",
        description: typeof detail === "string" ? detail : error.message,
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: resendInvitation } = useMutation({
    mutationFn: async (invitationId: string) =>
      await workspacesResendWorkspaceInvitation({ workspaceId, invitationId }),
    onSuccess: () =>
      toast({
        title: "Invitation resent",
        description: "The invitation email has been resent.",
      }),
    onError: (error: TracecatApiError) => {
      const detail = error.body?.detail
      toast({
        title: "Failed to resend invitation",
        description: typeof detail === "string" ? detail : error.message,
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: revokeInvitation } = useMutation({
    mutationFn: async (invitationId: string) =>
      await workspacesRevokeWorkspaceInvitation({ workspaceId, invitationId }),
    onSuccess: () => {
      invalidate()
      toast({
        title: "Invitation revoked",
        description: "Invitation has been revoked.",
      })
    },
    onError: (error: TracecatApiError) => {
      const detail = error.body?.detail
      toast({
        title: "Failed to revoke invitation",
        description: typeof detail === "string" ? detail : error.message,
        variant: "destructive",
      })
    },
  })

  return {
    invitations,
    invitationsLoading,
    invitationsError,
    createInvitationsBulk,
    createInvitationsBulkIsPending,
    resendInvitation,
    revokeInvitation,
  }
}
