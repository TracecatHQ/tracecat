"use client"

import {
  agentSkillsAddSkillTag,
  agentSkillsListSkillTags,
  agentSkillsRemoveSkillTag,
  type SkillTagCreate,
  type SkillTagRead,
  skillTagsCreateSkillTag,
  skillTagsDeleteSkillTag,
  skillTagsListSkillTags,
  skillTagsUpdateSkillTag,
  type TagCreate,
  type TagUpdate,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

async function listAllSkillTags(workspaceId: string): Promise<SkillTagRead[]> {
  const tags: SkillTagRead[] = []
  let cursor: string | undefined

  do {
    const response = await skillTagsListSkillTags({
      workspaceId,
      limit: 200,
      cursor,
    })
    tags.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return tags
}

/**
 * Manage the workspace skill tag catalog.
 *
 * @param workspaceId Workspace identifier.
 * @param options Query options.
 * @returns Tag query and mutation state.
 */
export function useSkillTagCatalog(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()
  const tagsQuery = useQuery<SkillTagRead[], TracecatApiError>({
    queryKey: ["skill-tags", workspaceId],
    queryFn: async () => await listAllSkillTags(workspaceId),
    enabled: options.enabled,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const createMutation = useMutation<SkillTagRead, TracecatApiError, TagCreate>(
    {
      mutationFn: async (requestBody) =>
        await skillTagsCreateSkillTag({ workspaceId, requestBody }),
      onSuccess: () => {
        invalidateSkillTagQueries(queryClient, workspaceId)
        toast({
          title: "Created skill tag",
          description: "Skill tag created successfully.",
        })
      },
      onError: (error) => {
        toast({
          title: "Failed to create skill tag",
          description: getApiErrorDetail(error) ?? "Please try again.",
          variant: "destructive",
        })
      },
    }
  )

  const updateMutation = useMutation<
    SkillTagRead,
    TracecatApiError,
    TagUpdate & { tagId: string }
  >({
    mutationFn: async ({ tagId, ...requestBody }) =>
      await skillTagsUpdateSkillTag({ workspaceId, tagId, requestBody }),
    onSuccess: () => {
      invalidateSkillTagQueries(queryClient, workspaceId)
      toast({
        title: "Updated skill tag",
        description: "Skill tag updated successfully.",
      })
    },
    onError: (error) => {
      toast({
        title: "Failed to update skill tag",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })

  const deleteMutation = useMutation<void, TracecatApiError, { tagId: string }>(
    {
      mutationFn: async ({ tagId }) =>
        await skillTagsDeleteSkillTag({ workspaceId, tagId }),
      onSuccess: () => {
        invalidateSkillTagQueries(queryClient, workspaceId)
        toast({
          title: "Deleted skill tag",
          description: "Skill tag deleted successfully.",
        })
      },
      onError: (error) => {
        toast({
          title: "Failed to delete skill tag",
          description: getApiErrorDetail(error) ?? "Please try again.",
          variant: "destructive",
        })
      },
    }
  )

  return {
    skillTags: tagsQuery.data,
    skillTagsIsLoading: tagsQuery.isLoading,
    skillTagsError: tagsQuery.error,
    createSkillTag: createMutation.mutateAsync,
    createSkillTagIsPending: createMutation.isPending,
    updateSkillTag: updateMutation.mutateAsync,
    updateSkillTagIsPending: updateMutation.isPending,
    deleteSkillTag: deleteMutation.mutateAsync,
    deleteSkillTagIsPending: deleteMutation.isPending,
  }
}

/**
 * List tags assigned to a skill.
 *
 * @param workspaceId Workspace identifier.
 * @param skillId Skill identifier.
 * @param options Query options.
 * @returns Assigned tag query state.
 */
export function useSkillTags(
  workspaceId?: string,
  skillId?: string | null,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const query = useQuery<SkillTagRead[], TracecatApiError>({
    queryKey: ["skill-tags-for-skill", workspaceId, skillId],
    queryFn: async () => {
      if (!workspaceId || !skillId) {
        throw new Error("workspaceId and skillId are required")
      }
      const tags: SkillTagRead[] = []
      let cursor: string | undefined
      do {
        const response = await agentSkillsListSkillTags({
          workspaceId,
          skillId,
          limit: 200,
          cursor,
        })
        tags.push(...response.items)
        cursor = response.next_cursor ?? undefined
      } while (cursor)
      return tags
    },
    enabled: enabled && Boolean(workspaceId && skillId),
  })

  return {
    skillTags: query.data,
    skillTagsIsLoading: query.isLoading,
    skillTagsError: query.error,
  }
}

/**
 * Add or remove a tag from a skill.
 *
 * @param workspaceId Workspace identifier.
 * @returns Skill tag association mutation state.
 */
export function useSkillTagMutations(workspaceId: string) {
  const queryClient = useQueryClient()
  const addMutation = useMutation<
    unknown,
    TracecatApiError,
    { skillId: string; requestBody: SkillTagCreate }
  >({
    mutationFn: async ({ skillId, requestBody }) =>
      await agentSkillsAddSkillTag({ workspaceId, skillId, requestBody }),
    onSuccess: (_result, variables) => {
      invalidateSkillTagAssociationQueries(
        queryClient,
        workspaceId,
        variables.skillId
      )
      toast({ title: "Tag added", description: "Skill tag added." })
    },
    onError: (error) => {
      toast({
        title: "Failed to add tag",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })

  const removeMutation = useMutation<
    void,
    TracecatApiError,
    { skillId: string; tagId: string }
  >({
    mutationFn: async ({ skillId, tagId }) =>
      await agentSkillsRemoveSkillTag({ workspaceId, skillId, tagId }),
    onSuccess: (_result, variables) => {
      invalidateSkillTagAssociationQueries(
        queryClient,
        workspaceId,
        variables.skillId
      )
      toast({ title: "Tag removed", description: "Skill tag removed." })
    },
    onError: (error) => {
      toast({
        title: "Failed to remove tag",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })

  return {
    addSkillTag: addMutation.mutateAsync,
    addSkillTagIsPending: addMutation.isPending,
    removeSkillTag: removeMutation.mutateAsync,
    removeSkillTagIsPending: removeMutation.isPending,
  }
}

function invalidateSkillTagQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string
) {
  void Promise.all([
    queryClient.invalidateQueries({ queryKey: ["skill-tags", workspaceId] }),
    queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] }),
    queryClient.invalidateQueries({
      queryKey: ["skill-directory-items", workspaceId],
    }),
  ])
}

function invalidateSkillTagAssociationQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string,
  skillId: string
) {
  void Promise.all([
    queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] }),
    queryClient.invalidateQueries({
      queryKey: ["skill-directory-items", workspaceId],
    }),
    queryClient.invalidateQueries({
      queryKey: ["skill-tags-for-skill", workspaceId, skillId],
    }),
    queryClient.invalidateQueries({
      queryKey: ["skill", workspaceId, skillId],
    }),
  ])
}
