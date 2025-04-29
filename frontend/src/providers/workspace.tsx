"use client"

import React, { createContext, ReactNode, useContext } from "react"
import {
  ApiError,
  WorkspaceMembershipRead,
  WorkspaceRead,
  workspacesCreateWorkspaceMembership,
  WorkspacesCreateWorkspaceMembershipData,
  WorkspacesCreateWorkspaceMembershipResponse,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesGetWorkspaceMembership,
  workspacesUpdateWorkspace,
  workspacesUpdateWorkspaceMembership,
  WorkspacesUpdateWorkspaceMembershipData,
  WorkspacesUpdateWorkspaceMembershipResponse,
  WorkspaceUpdate,
} from "@/client"
import { useAuth } from "@/providers/auth"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { retryHandler, TracecatApiError } from "@/lib/errors"
import { toast } from "@/components/ui/use-toast"

type WorkspaceContextType = {
  workspaceId: string
  workspace: WorkspaceRead | undefined
  workspaceLoading: boolean
  workspaceError: ApiError | null
  updateWorkspace: (params: WorkspaceUpdate) => Promise<unknown>
  // Memberships
  addWorkspaceMembership: (
    params: WorkspacesCreateWorkspaceMembershipData
  ) => Promise<WorkspacesCreateWorkspaceMembershipResponse>
  addWorkspaceMembershipIsPending: boolean
  removeWorkspaceMember: (userId: string) => Promise<unknown>
  updateWorkspaceMembership: (
    params: WorkspacesUpdateWorkspaceMembershipData
  ) => Promise<WorkspacesUpdateWorkspaceMembershipResponse>
  updateWorkspaceMembershipIsPending: boolean
  membership: WorkspaceMembershipRead | undefined
  membershipLoading: boolean
}

const WorkspaceContext = createContext<WorkspaceContextType | undefined>(
  undefined
)

/**
 * Provider for the current workspace.
 * This is so we can have a single source of truth for the current workspace.
 * Instead of having multiple components querying the workspace hooks.
 */
export function WorkspaceProvider({
  workspaceId,
  children,
}: {
  workspaceId: string
  children: ReactNode
}) {
  const { user } = useAuth()
  const userId = user?.id ?? ""
  const queryClient = useQueryClient()

  // Get workspace
  const {
    data: workspace,
    isLoading: workspaceLoading,
    error: workspaceError,
  } = useQuery<WorkspaceRead | undefined, ApiError>({
    queryKey: ["workspace", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        return undefined
      }
      return await workspacesGetWorkspace({ workspaceId })
    },
    retry: retryHandler,
  })

  // Update workspace
  const { mutateAsync: updateWorkspace } = useMutation({
    mutationFn: async (params: WorkspaceUpdate) =>
      await workspacesUpdateWorkspace({
        workspaceId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Updated workspace",
        description: "Your workspace has been updated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to update workspace:", error)
      toast({
        title: "Error update workspace",
        description: "Could not update workspace. Please try again.",
      })
    },
  })

  // Add member to workspace
  const {
    mutateAsync: addWorkspaceMembership,
    isPending: addWorkspaceMembershipIsPending,
  } = useMutation<
    WorkspacesCreateWorkspaceMembershipResponse,
    TracecatApiError,
    WorkspacesCreateWorkspaceMembershipData
  >({
    mutationFn: async (params: WorkspacesCreateWorkspaceMembershipData) =>
      await workspacesCreateWorkspaceMembership(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] })
      toast({
        title: "Successfully added member to workspace",
        description: "Added new member to workspace",
      })
    },
    onError: (error) => {
      console.error("Failed to add member to workspace:", error)
      switch (error.status) {
        case 409:
          toast({
            title: "User already belongs to this workspace",
            description:
              "The user you're trying to add is already in this workspace.",
          })
          break
        case 403:
          toast({
            title: "Unauthorized",
            description: "You cannot perform this action",
          })
          break
        default:
          toast({
            title: "Failed to add member to workspace",
            description: `${error.status}. Could not add member to workspace. Please try again.`,
            variant: "destructive",
          })
      }
    },
  })

  // Get membership
  const { data: membership, isLoading: membershipLoading } = useQuery<
    WorkspaceMembershipRead,
    ApiError
  >({
    queryKey: ["membership", workspaceId, userId],
    queryFn: async () =>
      await workspacesGetWorkspaceMembership({
        workspaceId,
        userId,
      }),
    retry: retryHandler,
    enabled: !!userId,
  })

  // Update member in workspace
  const {
    mutateAsync: updateWorkspaceMembership,
    isPending: updateWorkspaceMembershipIsPending,
  } = useMutation({
    mutationFn: async (params: WorkspacesUpdateWorkspaceMembershipData) =>
      await workspacesUpdateWorkspaceMembership(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["membership", workspaceId] })
      toast({
        title: "Successfully updated member in workspace",
        description: "Updated member in workspace",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          toast({
            title: "Failed to update member in workspace",
            description: `${error.status}. Could not update member in workspace. Please try again.`,
          })
      }
    },
  })

  // Remove member from workspace
  const { mutateAsync: removeWorkspaceMember } = useMutation<
    unknown,
    TracecatApiError,
    string
  >({
    mutationFn: async (userId: string) =>
      await workspacesDeleteWorkspaceMembership({
        workspaceId,
        userId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] })
      toast({
        title: "Successfully removed member to workspace",
        description: "Removed user from workspace",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          toast({
            title: "Failed to remove user from workspace",
            description: `${error.status}. Could not remove user from workspace. Please try again.`,
          })
      }
    },
  })

  return (
    <WorkspaceContext.Provider
      value={{
        workspaceId,
        workspace,
        workspaceLoading,
        workspaceError,
        addWorkspaceMembership,
        addWorkspaceMembershipIsPending,
        removeWorkspaceMember,
        updateWorkspaceMembership,
        updateWorkspaceMembershipIsPending,
        updateWorkspace,
        membership,
        membershipLoading,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  )
}

export const useWorkspace = () => {
  const context = useContext(WorkspaceContext)
  if (context === undefined) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider")
  }
  return context
}
