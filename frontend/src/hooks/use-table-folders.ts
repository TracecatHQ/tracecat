"use client"

import {
  type TableDirectoryItem,
  type TableFolderCreate,
  type TableFolderDirectoryItem,
  type TableFolderRead,
  type TableMoveToFolder,
  tableFoldersCreateFolder,
  tableFoldersDeleteFolder,
  tableFoldersGetDirectory,
  tableFoldersListFolders,
  tableFoldersMoveFolder,
  tableFoldersUpdateFolder,
  tablesMoveTable,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

async function listAllTableFolders(
  workspaceId: string
): Promise<TableFolderRead[]> {
  const folders: TableFolderRead[] = []
  let cursor: string | undefined

  do {
    const response = await tableFoldersListFolders({
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
 * A table directory response item.
 */
export type TableDirectoryEntry = TableDirectoryItem | TableFolderDirectoryItem

/**
 * Manage table folders and their folder-level mutations.
 *
 * @param workspaceId Workspace identifier.
 * @param options Query options.
 * @returns Folder query and mutation state.
 */
export function useTableFolders(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()
  const foldersQuery = useQuery<TableFolderRead[], TracecatApiError>({
    queryKey: ["table-folders", workspaceId],
    queryFn: async () => await listAllTableFolders(workspaceId),
    enabled: options.enabled,
  })

  const createMutation = useMutation<
    TableFolderRead,
    TracecatApiError,
    TableFolderCreate
  >({
    mutationFn: async (requestBody) =>
      await tableFoldersCreateFolder({ workspaceId, requestBody }),
    onSuccess: () => {
      invalidateTableOrganizationQueries(queryClient, workspaceId)
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
    TableFolderRead,
    TracecatApiError,
    { folderId: string; name: string }
  >({
    mutationFn: async ({ folderId, name }) =>
      await tableFoldersUpdateFolder({
        workspaceId,
        folderId,
        requestBody: { name },
      }),
    onSuccess: () => {
      invalidateTableOrganizationQueries(queryClient, workspaceId)
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
    TableFolderRead,
    TracecatApiError,
    { folderId: string; newParentPath: string | null }
  >({
    mutationFn: async ({ folderId, newParentPath }) =>
      await tableFoldersMoveFolder({
        workspaceId,
        folderId,
        requestBody: { new_parent_path: newParentPath },
      }),
    onSuccess: () => {
      invalidateTableOrganizationQueries(queryClient, workspaceId)
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
      await tableFoldersDeleteFolder({
        workspaceId,
        folderId,
        requestBody: { recursive },
      }),
    onSuccess: () => {
      invalidateTableOrganizationQueries(queryClient, workspaceId)
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
 * Load tables and folders in a table directory.
 *
 * @param path Directory path.
 * @param workspaceId Workspace identifier.
 * @param options Query options.
 * @returns Directory query state.
 */
export function useTableDirectoryItems(
  path: string,
  workspaceId?: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const query = useQuery<TableDirectoryEntry[], TracecatApiError>({
    queryKey: ["tables", workspaceId, "directory", path],
    queryFn: async () =>
      await tableFoldersGetDirectory({
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
 * Move a table into a folder or the workspace root.
 *
 * @param workspaceId Workspace identifier.
 * @returns Table move mutation state.
 */
export function useMoveTable(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    void,
    TracecatApiError,
    { tableId: string } & TableMoveToFolder
  >({
    mutationFn: async ({ tableId, ...requestBody }) =>
      await tablesMoveTable({ workspaceId, tableId, requestBody }),
    onSuccess: () => {
      invalidateTableOrganizationQueries(queryClient, workspaceId)
      toast({
        title: "Moved table",
        description: "Table moved successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Move failed",
        description: getApiErrorDetail(error) ?? "Failed to move table.",
        variant: "destructive",
      })
    },
  })

  return {
    moveTable: mutation.mutateAsync,
    moveTableIsPending: mutation.isPending,
  }
}

function invalidateTableOrganizationQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string
) {
  void Promise.all([
    queryClient.invalidateQueries({ queryKey: ["tables", workspaceId] }),
    queryClient.invalidateQueries({
      queryKey: ["tables", workspaceId, "directory"],
    }),
    queryClient.invalidateQueries({ queryKey: ["table-folders", workspaceId] }),
  ])
}
