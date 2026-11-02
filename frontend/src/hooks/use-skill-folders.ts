"use client"

import {
  agentSkillsMoveSkill,
  type SkillDirectoryItem,
  type SkillFolderCreate,
  type SkillFolderDirectoryItem,
  type SkillFolderRead,
  type SkillMoveToFolder,
  skillFoldersCreateFolder,
  skillFoldersDeleteFolder,
  skillFoldersGetDirectory,
  skillFoldersListFolders,
  skillFoldersMoveFolder,
  skillFoldersUpdateFolder,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

async function listAllSkillFolders(
  workspaceId: string
): Promise<SkillFolderRead[]> {
  const folders: SkillFolderRead[] = []
  let cursor: string | undefined

  do {
    const response = await skillFoldersListFolders({
      workspaceId,
      limit: 200,
      cursor,
    })
    folders.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return folders
}

/**
 * A skill directory response item.
 */
export type SkillDirectoryEntry = SkillDirectoryItem | SkillFolderDirectoryItem

/**
 * Manage skill folders and their folder-level mutations.
 *
 * @param workspaceId Workspace identifier.
 * @param options Query options.
 * @returns Folder query and mutation state.
 */
export function useSkillFolders(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()
  const foldersQuery = useQuery<SkillFolderRead[], TracecatApiError>({
    queryKey: ["skill-folders", workspaceId],
    queryFn: async () => await listAllSkillFolders(workspaceId),
    enabled: options.enabled,
  })

  const createMutation = useMutation<
    SkillFolderRead,
    TracecatApiError,
    SkillFolderCreate
  >({
    mutationFn: async (requestBody) =>
      await skillFoldersCreateFolder({ workspaceId, requestBody }),
    onSuccess: () => {
      invalidateSkillOrganizationQueries(queryClient, workspaceId)
      toast({
        title: "Created folder",
        description: "Folder created successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Failed to create folder",
        description:
          getApiErrorDetail(error) ??
          "An error occurred while creating the folder.",
        variant: "destructive",
      })
    },
  })

  const updateMutation = useMutation<
    SkillFolderRead,
    TracecatApiError,
    { folderId: string; name: string }
  >({
    mutationFn: async ({ folderId, name }) =>
      await skillFoldersUpdateFolder({
        workspaceId,
        folderId,
        requestBody: { name },
      }),
    onSuccess: () => {
      invalidateSkillOrganizationQueries(queryClient, workspaceId)
      toast({
        title: "Updated folder",
        description: "Folder updated successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Failed to update folder",
        description:
          getApiErrorDetail(error) ??
          "An error occurred while updating the folder.",
        variant: "destructive",
      })
    },
  })

  const moveMutation = useMutation<
    SkillFolderRead,
    TracecatApiError,
    { folderId: string; newParentPath: string | null }
  >({
    mutationFn: async ({ folderId, newParentPath }) =>
      await skillFoldersMoveFolder({
        workspaceId,
        folderId,
        requestBody: { new_parent_path: newParentPath },
      }),
    onSuccess: () => {
      invalidateSkillOrganizationQueries(queryClient, workspaceId)
      toast({
        title: "Moved folder",
        description: "Folder moved successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Failed to move folder",
        description:
          getApiErrorDetail(error) ??
          "An error occurred while moving the folder.",
        variant: "destructive",
      })
    },
  })

  const deleteMutation = useMutation<
    void,
    TracecatApiError,
    { folderId: string; recursive?: boolean }
  >({
    mutationFn: async ({ folderId, recursive = false }) =>
      await skillFoldersDeleteFolder({
        workspaceId,
        folderId,
        requestBody: { recursive },
      }),
    onSuccess: () => {
      invalidateSkillOrganizationQueries(queryClient, workspaceId)
      toast({
        title: "Deleted folder",
        description: "Folder deleted successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Failed to delete folder",
        description:
          getApiErrorDetail(error) ??
          "An error occurred while deleting the folder.",
        variant: "destructive",
      })
    },
  })

  return {
    folders: foldersQuery.data,
    foldersIsLoading: foldersQuery.isLoading,
    foldersError: foldersQuery.error,
    createFolder: createMutation.mutateAsync,
    createFolderIsPending: createMutation.isPending,
    updateFolder: updateMutation.mutateAsync,
    updateFolderIsPending: updateMutation.isPending,
    moveFolder: moveMutation.mutateAsync,
    moveFolderIsPending: moveMutation.isPending,
    deleteFolder: deleteMutation.mutateAsync,
    deleteFolderIsPending: deleteMutation.isPending,
  }
}

/**
 * Load skills and folders in a skill directory.
 *
 * @param path Directory path.
 * @param workspaceId Workspace identifier.
 * @param options Query options.
 * @returns Directory query state.
 */
export function useSkillDirectoryItems(
  path: string,
  workspaceId?: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const query = useQuery<SkillDirectoryEntry[], TracecatApiError>({
    queryKey: ["skill-directory-items", workspaceId, path],
    queryFn: async () =>
      await skillFoldersGetDirectory({
        workspaceId: workspaceId ?? "",
        path,
      }),
    enabled: enabled && Boolean(workspaceId),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return {
    directoryItems: query.data,
    directoryItemsIsLoading: query.isLoading,
    directoryItemsError: query.error,
  }
}

/**
 * Move a skill into a folder or the workspace root.
 *
 * @param workspaceId Workspace identifier.
 * @returns Skill move mutation state.
 */
export function useMoveSkill(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    void,
    TracecatApiError,
    { skillId: string } & SkillMoveToFolder
  >({
    mutationFn: async ({ skillId, ...requestBody }) =>
      await agentSkillsMoveSkill({ workspaceId, skillId, requestBody }),
    onSuccess: () => {
      invalidateSkillOrganizationQueries(queryClient, workspaceId)
      toast({
        title: "Moved skill",
        description: "Skill moved successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Move failed",
        description: getApiErrorDetail(error) ?? "Failed to move skill.",
        variant: "destructive",
      })
    },
  })

  return {
    moveSkill: mutation.mutateAsync,
    moveSkillIsPending: mutation.isPending,
  }
}

function invalidateSkillOrganizationQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string
) {
  void Promise.all([
    queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] }),
    queryClient.invalidateQueries({
      queryKey: ["skill-directory-items", workspaceId],
    }),
    queryClient.invalidateQueries({ queryKey: ["skill-folders", workspaceId] }),
  ])
}
