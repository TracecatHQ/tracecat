import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type AgentFolderCreate,
  type AgentFolderDirectoryItem,
  type AgentFolderRead,
  type AgentPresetCreate,
  type AgentPresetDirectoryItem,
  type AgentPresetMoveToFolder,
  type AgentPresetRead,
  type AgentPresetReadMinimal,
  type AgentPresetUpdate,
  type AgentPresetVersionDiff,
  type AgentPresetVersionReadMinimal,
  type AgentTagRead,
  agentFoldersCreateFolder,
  agentFoldersDeleteFolder,
  agentFoldersGetDirectory,
  agentFoldersListFolders,
  agentFoldersMoveFolder,
  agentFoldersUpdateFolder,
  agentPresetsCompareAgentPresetVersions,
  agentPresetsCreateAgentPreset,
  agentPresetsDeleteAgentPreset,
  agentPresetsGetAgentPreset,
  agentPresetsListAgentPresets,
  agentPresetsListAgentPresetVersions,
  agentPresetsMoveAgentPresetToFolder,
  agentPresetsRestoreAgentPresetVersion,
  agentPresetsUpdateAgentPreset,
  agentTagsCreateAgentTag,
  agentTagsDeleteAgentTag,
  agentTagsListAgentTags,
  agentTagsUpdateAgentTag,
  type TagCreate,
  type TagUpdate,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { retryHandler, type TracecatApiError } from "@/lib/errors"

export function useAgentPresets(
  workspaceId?: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: presets,
    isLoading: presetsIsLoading,
    error: presetsError,
    refetch: refetchPresets,
  } = useQuery<AgentPresetReadMinimal[], TracecatApiError>({
    queryKey: ["agent-presets", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("workspaceId is required to list agent presets")
      }
      return await agentPresetsListAgentPresets({ workspaceId })
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })

  return {
    presets,
    presetsIsLoading,
    presetsError,
    refetchPresets,
  }
}

export function useAgentPresetVersions(
  workspaceId: string,
  presetId?: string | null,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: versions,
    isLoading: versionsIsLoading,
    error: versionsError,
    refetch: refetchVersions,
  } = useQuery<AgentPresetVersionReadMinimal[], TracecatApiError>({
    queryKey: ["agent-preset-versions", workspaceId, presetId],
    queryFn: async () => {
      if (!workspaceId || !presetId) {
        throw new Error("workspaceId and presetId are required")
      }
      const versions: AgentPresetVersionReadMinimal[] = []
      let cursor: string | undefined

      do {
        const response = await agentPresetsListAgentPresetVersions({
          workspaceId,
          presetId,
          limit: 200,
          cursor,
        })
        versions.push(...response.items)
        cursor = response.next_cursor ?? undefined
      } while (cursor)

      return versions
    },
    enabled: enabled && Boolean(workspaceId) && Boolean(presetId),
    retry: retryHandler,
  })

  return {
    versions,
    versionsIsLoading,
    versionsError,
    refetchVersions,
  }
}

export function useAgentPreset(
  workspaceId: string,
  presetId?: string | null,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: preset,
    isLoading: presetIsLoading,
    error: presetError,
    refetch: refetchPreset,
  } = useQuery<AgentPresetRead, TracecatApiError>({
    queryKey: ["agent-preset", workspaceId, presetId],
    queryFn: async () => {
      if (!workspaceId || !presetId) {
        throw new Error("workspaceId and presetId are required")
      }
      return await agentPresetsGetAgentPreset({ workspaceId, presetId })
    },
    enabled: enabled && Boolean(workspaceId) && Boolean(presetId),
    retry: retryHandler,
  })

  return {
    preset,
    presetIsLoading,
    presetError,
    refetchPreset,
  }
}

export function useCompareAgentPresetVersions(
  workspaceId: string,
  presetId?: string | null,
  baseVersionId?: string | null,
  compareToId?: string | null,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const {
    data: diff,
    isLoading: diffIsLoading,
    error: diffError,
    refetch: refetchDiff,
  } = useQuery<AgentPresetVersionDiff, TracecatApiError>({
    queryKey: [
      "agent-preset-version-diff",
      workspaceId,
      presetId,
      baseVersionId,
      compareToId,
    ],
    queryFn: async () => {
      if (!workspaceId || !presetId || !baseVersionId || !compareToId) {
        throw new Error(
          "workspaceId, presetId, baseVersionId, and compareToId are required"
        )
      }
      return await agentPresetsCompareAgentPresetVersions({
        workspaceId,
        presetId,
        versionId: baseVersionId,
        compareTo: compareToId,
      })
    },
    enabled:
      enabled &&
      Boolean(workspaceId) &&
      Boolean(presetId) &&
      Boolean(baseVersionId) &&
      Boolean(compareToId),
    retry: retryHandler,
  })

  return {
    diff,
    diffIsLoading,
    diffError,
    refetchDiff,
  }
}

export function useCreateAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createAgentPreset,
    isPending: createAgentPresetIsPending,
    error: createAgentPresetError,
  } = useMutation<AgentPresetRead, TracecatApiError, AgentPresetCreate>({
    mutationFn: async (payload) =>
      await agentPresetsCreateAgentPreset({
        workspaceId,
        requestBody: payload,
      }),
    onSuccess: (preset) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset-versions", workspaceId, preset.id],
      })
      toast({
        title: "Agent preset created",
        description: `Saved ${preset.name}`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to create agent preset."
      toast({
        title: "Create failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    createAgentPreset,
    createAgentPresetIsPending,
    createAgentPresetError,
  }
}

export function useUpdateAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateAgentPreset,
    isPending: updateAgentPresetIsPending,
    error: updateAgentPresetError,
  } = useMutation<
    AgentPresetRead,
    TracecatApiError,
    AgentPresetUpdate & { presetId: string }
  >({
    mutationFn: async ({ presetId, ...requestBody }) =>
      await agentPresetsUpdateAgentPreset({
        workspaceId,
        presetId,
        requestBody,
      }),
    onSuccess: (preset) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset", workspaceId, preset.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset-versions", workspaceId, preset.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["workspace-agent-providers-status", workspaceId],
      })
      toast({
        title: "Agent preset updated",
        description: `Saved ${preset.name}`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to update agent preset."
      toast({
        title: "Update failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    updateAgentPreset,
    updateAgentPresetIsPending,
    updateAgentPresetError,
  }
}

export function useDeleteAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteAgentPreset,
    isPending: deleteAgentPresetIsPending,
    error: deleteAgentPresetError,
  } = useMutation<
    void,
    TracecatApiError,
    { presetId: string; presetName?: string }
  >({
    mutationFn: async ({ presetId }) =>
      await agentPresetsDeleteAgentPreset({
        workspaceId,
        presetId,
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      const label = variables.presetName ?? variables.presetId
      toast({
        title: "Agent preset deleted",
        description: `Removed ${label}`,
      })
    },
    onError: (error, variables) => {
      const label =
        variables?.presetName ?? variables?.presetId ?? "agent preset"
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to delete agent preset."
      toast({
        title: "Delete failed",
        description: `${label}: ${detail}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteAgentPreset,
    deleteAgentPresetIsPending,
    deleteAgentPresetError,
  }
}

export function useRestoreAgentPresetVersion(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: restoreAgentPresetVersion,
    isPending: restoreAgentPresetVersionIsPending,
    error: restoreAgentPresetVersionError,
  } = useMutation<
    AgentPresetRead,
    TracecatApiError,
    { presetId: string; versionId: string }
  >({
    mutationFn: async ({ presetId, versionId }) =>
      await agentPresetsRestoreAgentPresetVersion({
        workspaceId,
        presetId,
        versionId,
      }),
    onSuccess: (preset) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset", workspaceId, preset.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset-versions", workspaceId, preset.id],
      })
      toast({
        title: "Version restored",
        description: `${preset.name} now points to the selected version.`,
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to restore preset version."
      toast({
        title: "Restore failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    restoreAgentPresetVersion,
    restoreAgentPresetVersionIsPending,
    restoreAgentPresetVersionError,
  }
}

export type AgentDirectoryItem =
  | AgentFolderDirectoryItem
  | AgentPresetDirectoryItem

/** CRUD hook for agent folders. Mirrors `useFolders` from hooks.tsx. */
export function useAgentFolders(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()

  // List folders
  const {
    data: folders,
    isLoading: foldersIsLoading,
    error: foldersError,
  } = useQuery<AgentFolderRead[]>({
    queryKey: ["agent-folders", workspaceId],
    queryFn: async () => await agentFoldersListFolders({ workspaceId }),
    enabled: options.enabled,
  })

  // List subfolders under root
  const {
    data: subFolders,
    isLoading: subFoldersIsLoading,
    error: subFoldersError,
    refetch: refetchSubFolders,
  } = useQuery<AgentFolderRead[]>({
    queryKey: ["agent-folders", workspaceId, "parent"],
    queryFn: async () =>
      await agentFoldersListFolders({
        workspaceId,
        parentPath: "/",
      }),
    enabled: options.enabled && !!workspaceId,
  })

  // Create folder
  const {
    mutateAsync: createFolder,
    isPending: createFolderIsPending,
    error: createFolderError,
  } = useMutation({
    mutationFn: async (params: AgentFolderCreate) =>
      await agentFoldersCreateFolder({
        workspaceId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-folders", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["agent-directory-items"] })
      toast({
        title: "Created folder",
        description: "Folder created successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          console.error("Error creating folder", error)
          return toast({
            title: "Error creating folder",
            description:
              "A folder with this name already exists at this location.",
          })
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
        default:
          console.error("Failed to create folder", error)
          return toast({
            title: "Failed to create folder",
            description: `An error occurred while creating the folder: ${error.body.detail}`,
          })
      }
    },
  })

  // Update folder (rename)
  const {
    mutateAsync: updateFolder,
    isPending: updateFolderIsPending,
    error: updateFolderError,
  } = useMutation({
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
      queryClient.invalidateQueries({
        queryKey: ["agent-folders", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["agent-directory-items"] })
      toast({
        title: "Updated folder",
        description: "Folder updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          console.error("Error updating folder", error)
          toast({
            title: "Error updating folder",
            description:
              "A folder with this name already exists at this location.",
          })
          break
        default:
          console.error("Error updating folder", error)
          toast({
            title: "Error updating folder",
            description: `An error occurred while updating the folder: ${error.body.detail}`,
          })
          break
      }
    },
  })

  // Move folder
  const {
    mutateAsync: moveFolder,
    isPending: moveFolderIsPending,
    error: moveFolderError,
  } = useMutation({
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
      queryClient.invalidateQueries({
        queryKey: ["agent-folders", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["agent-directory-items"] })
      toast({
        title: "Moved folder",
        description: "Folder moved successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Error moving folder", error)
      toast({
        title: "Error moving folder",
        description: `An error occurred while moving the folder: ${error.body.detail}`,
      })
    },
  })

  // Delete folder
  const {
    mutateAsync: deleteFolder,
    isPending: deleteFolderIsPending,
    error: deleteFolderError,
  } = useMutation({
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
      queryClient.invalidateQueries({
        queryKey: ["agent-folders", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["agent-directory-items"] })
      toast({
        title: "Deleted folder",
        description: "Folder deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Cannot delete folder",
            description: String(error.body.detail),
          })
          break
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Error deleting folder", error)
          toast({
            title: "Failed to delete folder",
            description: `An error occurred while deleting the folder: ${error.body.detail}`,
          })
          break
      }
    },
  })

  return {
    // List
    folders,
    foldersIsLoading,
    foldersError,
    // List subfolders
    subFolders,
    subFoldersIsLoading,
    subFoldersError,
    refetchSubFolders,
    // Create
    createFolder,
    createFolderIsPending,
    createFolderError,
    // Update
    updateFolder,
    updateFolderIsPending,
    updateFolderError,
    // Move
    moveFolder,
    moveFolderIsPending,
    moveFolderError,
    // Delete
    deleteFolder,
    deleteFolderIsPending,
    deleteFolderError,
  }
}

/** Fetch the directory listing for agent folders and presets at a given path. */
export function useAgentDirectoryItems(
  path: string,
  workspaceId?: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const {
    data: directoryItems,
    isLoading: directoryItemsIsLoading,
    error: directoryItemsError,
  } = useQuery<AgentDirectoryItem[], TracecatApiError>({
    enabled: enabled && !!workspaceId,
    queryKey: ["agent-directory-items", workspaceId, path],
    queryFn: async () =>
      await agentFoldersGetDirectory({
        path,
        workspaceId: workspaceId ?? "",
      }),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  return {
    directoryItems,
    directoryItemsIsLoading,
    directoryItemsError,
  }
}

/** CRUD hook for the agent tag catalog. Mirrors `useCaseTagCatalog`. */
export function useAgentTagCatalog(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()

  const {
    data: agentTags,
    isLoading: agentTagsIsLoading,
    error: agentTagsError,
  } = useQuery<AgentTagRead[]>({
    queryKey: ["agent-tags", workspaceId],
    queryFn: async () => await agentTagsListAgentTags({ workspaceId }),
    enabled: options.enabled,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const {
    mutateAsync: createAgentTag,
    isPending: createAgentTagIsPending,
    error: createAgentTagError,
  } = useMutation({
    mutationFn: async (params: TagCreate) =>
      await agentTagsCreateAgentTag({ workspaceId, requestBody: params }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-tags", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-directory-items", workspaceId],
      })
      toast({
        title: "Created agent tag",
        description: "Agent tag created successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          toast({
            title: "Error creating agent tag",
            description: String(error.body.detail),
          })
          break
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to create agent tag", error)
          toast({
            title: "Failed to create agent tag",
            description: `An error occurred while creating the agent tag: ${error.body.detail}`,
          })
      }
    },
  })

  const {
    mutateAsync: updateAgentTag,
    isPending: updateAgentTagIsPending,
    error: updateAgentTagError,
  } = useMutation({
    mutationFn: async ({
      tagId,
      ...requestBody
    }: TagUpdate & { tagId: string }) =>
      await agentTagsUpdateAgentTag({ tagId, workspaceId, requestBody }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-tags", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-directory-items", workspaceId],
      })
      toast({
        title: "Updated agent tag",
        description: "Agent tag updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          toast({
            title: "Error updating agent tag",
            description: String(error.body.detail),
          })
          break
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
      }
    },
  })

  const {
    mutateAsync: deleteAgentTag,
    isPending: deleteAgentTagIsPending,
    error: deleteAgentTagError,
  } = useMutation({
    mutationFn: async ({ tagId }: { tagId: string }) =>
      await agentTagsDeleteAgentTag({ tagId, workspaceId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-tags", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-directory-items", workspaceId],
      })
      toast({
        title: "Deleted agent tag",
        description: "Agent tag deleted successfully.",
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
      }
    },
  })

  return {
    agentTags,
    agentTagsIsLoading,
    agentTagsError,
    createAgentTag,
    createAgentTagIsPending,
    createAgentTagError,
    updateAgentTag,
    updateAgentTagIsPending,
    updateAgentTagError,
    deleteAgentTag,
    deleteAgentTagIsPending,
    deleteAgentTagError,
  }
}

/** Move an agent preset into a folder. */
export function useMoveAgentPreset(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: moveAgentPreset,
    isPending: moveAgentPresetIsPending,
    error: moveAgentPresetError,
  } = useMutation<
    void,
    TracecatApiError,
    { presetId: string } & AgentPresetMoveToFolder
  >({
    mutationFn: async ({ presetId, ...requestBody }) =>
      await agentPresetsMoveAgentPresetToFolder({
        presetId,
        workspaceId,
        requestBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["agent-directory-items"] })
      toast({
        title: "Moved agent preset",
        description: "Agent preset moved successfully.",
      })
    },
    onError: (error) => {
      const detail =
        typeof error.body?.detail === "string"
          ? error.body.detail
          : "Failed to move agent preset."
      toast({
        title: "Move failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    moveAgentPreset,
    moveAgentPresetIsPending,
    moveAgentPresetError,
  }
}
