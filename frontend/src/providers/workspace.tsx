"use client"

import React, { createContext, ReactNode, useContext } from "react"
import {
  ApiError,
  UpdateWorkspaceParams,
  WorkspaceResponse,
  workspacesCreateWorkspaceMembership,
  workspacesDeleteWorkspaceMembership,
  workspacesGetWorkspace,
  workspacesUpdateWorkspace,
} from "@/client"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { retryHandler } from "@/lib/errors"
import { toast } from "@/components/ui/use-toast"

type WorkspaceContextType = {
  workspaceId: string
  workspace: WorkspaceResponse | undefined
  workspaceLoading: boolean
  workspaceError: ApiError | null
  addWorkspaceMember: (userId: string) => Promise<unknown>
  removeWorkspaceMember: (userId: string) => Promise<unknown>
  updateWorkspace: (params: UpdateWorkspaceParams) => Promise<unknown>
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
  const queryClient = useQueryClient()

  // Get workspace
  const {
    data: workspace,
    isLoading: workspaceLoading,
    error: workspaceError,
  } = useQuery<WorkspaceResponse | undefined, ApiError>({
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
    mutationFn: async (params: UpdateWorkspaceParams) =>
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

  // Add meber to workspace
  const { mutateAsync: addWorkspaceMember } = useMutation<
    unknown,
    ApiError,
    string
  >({
    mutationFn: async (userId: string) =>
      await workspacesCreateWorkspaceMembership({
        workspaceId,
        requestBody: {
          user_id: userId,
        },
      }),
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

  // Remove member from workspace
  const { mutateAsync: removeWorkspaceMember } = useMutation<
    unknown,
    ApiError,
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
    onError: (error) => {
      console.error("Failed to remove user from workspace:", error)
      toast({
        title: "Failed to remove user from workspace:",
        description: "Could not remove user from workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  return (
    <WorkspaceContext.Provider
      value={{
        workspaceId,
        workspace,
        workspaceLoading,
        workspaceError,
        addWorkspaceMember,
        removeWorkspaceMember,
        updateWorkspace,
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
