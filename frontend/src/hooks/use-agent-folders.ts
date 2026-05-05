"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type AgentFolderCreate,
  type AgentFolderDirectoryItem,
  type AgentFolderRead,
  type AgentPresetDirectoryItem,
  agentFoldersCreateFolder,
  agentFoldersDeleteFolder,
  agentFoldersGetDirectory,
  agentFoldersListFolders,
  agentFoldersMoveFolder,
  agentFoldersUpdateFolder,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"

export type AgentDirectoryItem =
  | AgentFolderDirectoryItem
  | AgentPresetDirectoryItem

const FOLDERS_KEY = "agent-folders"
const DIRECTORY_KEY = "agent-directory-items"

/**
 * Manage agent preset folders for a workspace.
 *
 * Mirrors the workflow folder hook so the agents catalog feels identical to
 * users coming from /workflows.
 */
export function useAgentFolders(
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const queryClient = useQueryClient()

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [FOLDERS_KEY, workspaceId] })
    queryClient.invalidateQueries({ queryKey: [DIRECTORY_KEY, workspaceId] })
  }

  const {
    data: folders,
    isLoading: foldersIsLoading,
    error: foldersError,
  } = useQuery<AgentFolderRead[]>({
    queryKey: [FOLDERS_KEY, workspaceId],
    queryFn: async () => await agentFoldersListFolders({ workspaceId }),
    enabled: enabled && !!workspaceId,
  })

  const { mutateAsync: createFolder, isPending: createFolderIsPending } =
    useMutation({
      mutationFn: async (params: AgentFolderCreate) =>
        await agentFoldersCreateFolder({ workspaceId, requestBody: params }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Created folder" })
      },
      onError: (error: TracecatApiError) => {
        if (error.status === 409) {
          toast({
            title: "Folder already exists",
            description: "Pick a different name and try again.",
          })
        } else if (error.status === 403) {
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
        } else {
          toast({
            title: "Failed to create folder",
            description: String(error.body.detail ?? error.message),
          })
        }
      },
    })

  const { mutateAsync: updateFolder, isPending: updateFolderIsPending } =
    useMutation({
      mutationFn: async ({
        folderId,
        name,
      }: {
        folderId: string
        name: string
      }) =>
        await agentFoldersUpdateFolder({
          folderId,
          workspaceId,
          requestBody: { name },
        }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Renamed folder" })
      },
      onError: (error: TracecatApiError) => {
        if (error.status === 409) {
          toast({
            title: "Folder already exists",
            description: "Pick a different name and try again.",
          })
        } else {
          toast({
            title: "Failed to rename folder",
            description: String(error.body.detail ?? error.message),
          })
        }
      },
    })

  const { mutateAsync: moveFolder, isPending: moveFolderIsPending } =
    useMutation({
      mutationFn: async ({
        folderId,
        newParentPath,
      }: {
        folderId: string
        newParentPath: string | null
      }) =>
        await agentFoldersMoveFolder({
          folderId,
          workspaceId,
          requestBody: { new_parent_path: newParentPath },
        }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Moved folder" })
      },
      onError: (error: TracecatApiError) => {
        toast({
          title: "Failed to move folder",
          description: String(error.body.detail ?? error.message),
        })
      },
    })

  const { mutateAsync: deleteFolder, isPending: deleteFolderIsPending } =
    useMutation({
      mutationFn: async ({
        folderId,
        recursive = false,
      }: {
        folderId: string
        recursive?: boolean
      }) =>
        await agentFoldersDeleteFolder({
          folderId,
          workspaceId,
          requestBody: { recursive },
        }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Deleted folder" })
      },
      onError: (error: TracecatApiError) => {
        if (error.status === 400) {
          toast({
            title: "Cannot delete folder",
            description: String(error.body.detail),
          })
        } else {
          toast({
            title: "Failed to delete folder",
            description: String(error.body.detail ?? error.message),
          })
        }
      },
    })

  return {
    folders,
    foldersIsLoading,
    foldersError,
    createFolder,
    createFolderIsPending,
    updateFolder,
    updateFolderIsPending,
    moveFolder,
    moveFolderIsPending,
    deleteFolder,
    deleteFolderIsPending,
  }
}

/**
 * Fetch directory items (folders and presets) at a given path.
 */
export function useAgentDirectory(
  path: string,
  workspaceId?: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const {
    data: items,
    isLoading,
    error,
  } = useQuery<AgentDirectoryItem[]>({
    enabled: enabled && !!workspaceId,
    queryKey: [DIRECTORY_KEY, workspaceId, path],
    queryFn: async () =>
      await agentFoldersGetDirectory({ path, workspaceId: workspaceId ?? "" }),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return { items, isLoading, error }
}

/** Predicate for narrowing AgentDirectoryItem to folder. */
export function isAgentFolderItem(
  item: AgentDirectoryItem
): item is AgentFolderDirectoryItem {
  return item.type === "folder"
}

/** Predicate for narrowing AgentDirectoryItem to preset. */
export function isAgentPresetItem(
  item: AgentDirectoryItem
): item is AgentPresetDirectoryItem {
  return item.type === "preset"
}
