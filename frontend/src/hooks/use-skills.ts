"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  agentSkillsCreateSkill,
  agentSkillsCreateSkillDraftUpload,
  agentSkillsGetSkill,
  agentSkillsGetSkillDraft,
  agentSkillsGetSkillDraftFile,
  agentSkillsListSkills,
  agentSkillsListSkillVersions,
  agentSkillsPatchSkillDraft,
  agentSkillsPublishSkill,
  agentSkillsRestoreSkillVersion,
  agentSkillsUploadSkill,
  type SkillCreate,
  type SkillDraftFileRead,
  type SkillDraftPatch,
  type SkillDraftRead,
  type SkillRead,
  type SkillUpload,
  type SkillUploadSessionCreate,
  type SkillUploadSessionRead,
  type SkillVersionRead,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  getApiErrorDetail,
  retryHandler,
  type TracecatApiError,
} from "@/lib/errors"

/**
 * List all skills in the current workspace.
 *
 * @param workspaceId Workspace identifier.
 * @returns Flattened paginated skill list query state.
 *
 * @example
 * const { skills } = useSkills(workspaceId)
 */
export function useSkills(workspaceId?: string) {
  const query = useQuery<SkillRead[], TracecatApiError>({
    queryKey: ["skills", workspaceId],
    queryFn: async () => {
      if (!workspaceId) {
        throw new Error("workspaceId is required to list skills")
      }

      const items: SkillRead[] = []
      let cursor: string | undefined
      do {
        const page = await agentSkillsListSkills({
          workspaceId,
          limit: 200,
          cursor,
        })
        items.push(...page.items)
        cursor = page.next_cursor ?? undefined
      } while (cursor)

      return items
    },
    enabled: Boolean(workspaceId),
    retry: retryHandler,
  })

  return {
    skills: query.data,
    skillsLoading: query.isLoading,
    skillsError: query.error,
  }
}

/**
 * Get one skill summary.
 *
 * @param workspaceId Workspace identifier.
 * @param skillId Skill identifier.
 * @returns Skill detail query state.
 */
export function useSkill(workspaceId?: string, skillId?: string | null) {
  const query = useQuery<SkillRead, TracecatApiError>({
    queryKey: ["skill", workspaceId, skillId],
    queryFn: async () => {
      if (!workspaceId || !skillId) {
        throw new Error("workspaceId and skillId are required")
      }
      return await agentSkillsGetSkill({ workspaceId, skillId })
    },
    enabled: Boolean(workspaceId && skillId),
    retry: retryHandler,
  })

  return {
    skill: query.data,
    skillLoading: query.isLoading,
    skillError: query.error,
  }
}

/**
 * Get the mutable working copy for a skill.
 *
 * @param workspaceId Workspace identifier.
 * @param skillId Skill identifier.
 * @returns Draft query state.
 */
export function useSkillDraft(workspaceId?: string, skillId?: string | null) {
  const query = useQuery<SkillDraftRead, TracecatApiError>({
    queryKey: ["skill-draft", workspaceId, skillId],
    queryFn: async () => {
      if (!workspaceId || !skillId) {
        throw new Error("workspaceId and skillId are required")
      }
      return await agentSkillsGetSkillDraft({ workspaceId, skillId })
    },
    enabled: Boolean(workspaceId && skillId),
    retry: retryHandler,
  })

  return {
    draft: query.data,
    draftLoading: query.isLoading,
    draftError: query.error,
  }
}

/**
 * Get one working-copy file.
 *
 * @param workspaceId Workspace identifier.
 * @param skillId Skill identifier.
 * @param path Relative file path.
 * @returns Draft file query state.
 */
export function useSkillDraftFile(
  workspaceId?: string,
  skillId?: string | null,
  path?: string | null
) {
  const query = useQuery<SkillDraftFileRead, TracecatApiError>({
    queryKey: ["skill-draft-file", workspaceId, skillId, path],
    queryFn: async () => {
      if (!workspaceId || !skillId || !path) {
        throw new Error("workspaceId, skillId, and path are required")
      }
      return await agentSkillsGetSkillDraftFile({ workspaceId, skillId, path })
    },
    enabled: Boolean(workspaceId && skillId && path),
    retry: retryHandler,
  })

  return {
    draftFile: query.data,
    draftFileLoading: query.isLoading,
    draftFileError: query.error,
  }
}

/**
 * List all published versions for a skill.
 *
 * @param workspaceId Workspace identifier.
 * @param skillId Skill identifier.
 * @returns Flattened version list query state.
 */
export function useSkillVersions(
  workspaceId?: string,
  skillId?: string | null
) {
  const query = useQuery<SkillVersionRead[], TracecatApiError>({
    queryKey: ["skill-versions", workspaceId, skillId],
    queryFn: async () => {
      if (!workspaceId || !skillId) {
        throw new Error("workspaceId and skillId are required")
      }

      const items: SkillVersionRead[] = []
      let cursor: string | undefined
      do {
        const page = await agentSkillsListSkillVersions({
          workspaceId,
          skillId,
          limit: 200,
          cursor,
        })
        items.push(...page.items)
        cursor = page.next_cursor ?? undefined
      } while (cursor)

      return items
    },
    enabled: Boolean(workspaceId && skillId),
    retry: retryHandler,
  })

  return {
    versions: query.data,
    versionsLoading: query.isLoading,
    versionsError: query.error,
  }
}

/**
 * Create a new empty skill.
 *
 * @param workspaceId Workspace identifier.
 * @returns Skill creation mutation state.
 */
export function useCreateSkill(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<SkillRead, TracecatApiError, SkillCreate>({
    mutationFn: async (requestBody) =>
      await agentSkillsCreateSkill({ workspaceId, requestBody }),
    onSuccess: (skill) => {
      queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["skill", workspaceId, skill.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["skill-draft", workspaceId, skill.id],
      })
      toast({
        title: "Skill created",
        description: `Created ${skill.title ?? skill.slug}`,
      })
    },
    onError: (error) => {
      toast({
        title: "Create failed",
        description: getApiErrorDetail(error) ?? "Failed to create skill.",
        variant: "destructive",
      })
    },
  })

  return {
    createSkill: mutation.mutateAsync,
    createSkillPending: mutation.isPending,
    createSkillError: mutation.error,
  }
}

/**
 * Upload a skill directory as a new skill.
 *
 * @param workspaceId Workspace identifier.
 * @returns Skill upload mutation state.
 */
export function useUploadSkill(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<SkillRead, TracecatApiError, SkillUpload>({
    mutationFn: async (requestBody) =>
      await agentSkillsUploadSkill({ workspaceId, requestBody }),
    onSuccess: (skill) => {
      queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["skill", workspaceId, skill.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["skill-draft", workspaceId, skill.id],
      })
      toast({
        title: "Skill uploaded",
        description: `Imported ${skill.title ?? skill.slug}`,
      })
    },
    onError: (error) => {
      toast({
        title: "Upload failed",
        description: getApiErrorDetail(error) ?? "Failed to upload skill.",
        variant: "destructive",
      })
    },
  })

  return {
    uploadSkill: mutation.mutateAsync,
    uploadSkillPending: mutation.isPending,
    uploadSkillError: mutation.error,
  }
}

/**
 * Apply optimistic working-copy mutations.
 *
 * @param workspaceId Workspace identifier.
 * @returns Draft patch mutation state.
 */
export function usePatchSkillDraft(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    SkillDraftRead,
    TracecatApiError,
    { skillId: string; requestBody: SkillDraftPatch }
  >({
    mutationFn: async ({ skillId, requestBody }) =>
      await agentSkillsPatchSkillDraft({ workspaceId, skillId, requestBody }),
    onSuccess: (draft, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["skill", workspaceId, variables.skillId],
      })
      queryClient.setQueryData(
        ["skill-draft", workspaceId, variables.skillId],
        draft
      )
      queryClient.invalidateQueries({
        queryKey: ["skill-draft-file", workspaceId, variables.skillId],
      })
      toast({
        title: "Working copy saved",
        description: `Revision ${draft.draft_revision}`,
      })
    },
    onError: (error) => {
      toast({
        title: "Save failed",
        description: getApiErrorDetail(error) ?? "Failed to save working copy.",
        variant: "destructive",
      })
    },
  })

  return {
    patchSkillDraft: mutation.mutateAsync,
    patchSkillDraftPending: mutation.isPending,
    patchSkillDraftError: mutation.error,
  }
}

/**
 * Create a staged upload session for a binary file replacement.
 *
 * @param workspaceId Workspace identifier.
 * @returns Upload session mutation state.
 */
export function useCreateSkillDraftUpload(workspaceId: string) {
  const mutation = useMutation<
    SkillUploadSessionRead,
    TracecatApiError,
    { skillId: string; requestBody: SkillUploadSessionCreate }
  >({
    mutationFn: async ({ skillId, requestBody }) =>
      await agentSkillsCreateSkillDraftUpload({
        workspaceId,
        skillId,
        requestBody,
      }),
  })

  return {
    createSkillDraftUpload: mutation.mutateAsync,
    createSkillDraftUploadPending: mutation.isPending,
    createSkillDraftUploadError: mutation.error,
  }
}

/**
 * Publish the current working copy into a new version.
 *
 * @param workspaceId Workspace identifier.
 * @returns Publish mutation state.
 */
export function usePublishSkill(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    SkillVersionRead,
    TracecatApiError,
    { skillId: string }
  >({
    mutationFn: async ({ skillId }) =>
      await agentSkillsPublishSkill({ workspaceId, skillId }),
    onSuccess: (version, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["skills", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["skill", workspaceId, variables.skillId],
      })
      queryClient.invalidateQueries({
        queryKey: ["skill-draft", workspaceId, variables.skillId],
      })
      queryClient.invalidateQueries({
        queryKey: ["skill-versions", workspaceId, variables.skillId],
      })
      toast({
        title: "Version published",
        description: `Published v${version.version}`,
      })
    },
    onError: (error) => {
      toast({
        title: "Publish failed",
        description: getApiErrorDetail(error) ?? "Failed to publish skill.",
        variant: "destructive",
      })
    },
  })

  return {
    publishSkill: mutation.mutateAsync,
    publishSkillPending: mutation.isPending,
    publishSkillError: mutation.error,
  }
}

/**
 * Restore a published version into the working copy.
 *
 * @param workspaceId Workspace identifier.
 * @returns Restore mutation state.
 */
export function useRestoreSkillVersion(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    SkillDraftRead,
    TracecatApiError,
    { skillId: string; versionId: string }
  >({
    mutationFn: async ({ skillId, versionId }) =>
      await agentSkillsRestoreSkillVersion({ workspaceId, skillId, versionId }),
    onSuccess: (draft, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["skills", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["skill", workspaceId, variables.skillId],
      })
      queryClient.setQueryData(
        ["skill-draft", workspaceId, variables.skillId],
        draft
      )
      queryClient.invalidateQueries({
        queryKey: ["skill-draft-file", workspaceId, variables.skillId],
      })
      toast({
        title: "Working copy restored",
        description: `Restored revision ${draft.draft_revision}`,
      })
    },
    onError: (error) => {
      toast({
        title: "Restore failed",
        description: getApiErrorDetail(error) ?? "Failed to restore version.",
        variant: "destructive",
      })
    },
  })

  return {
    restoreSkillVersion: mutation.mutateAsync,
    restoreSkillVersionPending: mutation.isPending,
    restoreSkillVersionError: mutation.error,
  }
}
