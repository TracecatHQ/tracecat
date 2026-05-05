"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  agentPresetsAddPresetTag,
  agentPresetsListPresetTags,
  agentPresetsRemovePresetTag,
  agentTagsCreateTag,
  agentTagsDeleteTag,
  agentTagsListTags,
  agentTagsUpdateTag,
  type TagCreate,
  type TagRead,
  type TagUpdate,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"

const TAGS_KEY = "agent-tags"
const PRESET_TAGS_KEY = "agent-preset-tags"
const DIRECTORY_KEY = "agent-directory-items"

/**
 * Manage agent tag definitions for a workspace.
 */
export function useAgentTags(
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const queryClient = useQueryClient()

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [TAGS_KEY, workspaceId] })
    queryClient.invalidateQueries({ queryKey: [PRESET_TAGS_KEY] })
    queryClient.invalidateQueries({ queryKey: [DIRECTORY_KEY, workspaceId] })
  }

  const {
    data: tags,
    isLoading: tagsIsLoading,
    error: tagsError,
  } = useQuery<TagRead[]>({
    queryKey: [TAGS_KEY, workspaceId],
    queryFn: async () => await agentTagsListTags({ workspaceId }),
    enabled: enabled && !!workspaceId,
  })

  const { mutateAsync: createTag, isPending: createTagIsPending } = useMutation(
    {
      mutationFn: async (params: TagCreate) =>
        await agentTagsCreateTag({ workspaceId, requestBody: params }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Created tag" })
      },
      onError: (error: TracecatApiError) => {
        if (error.status === 409) {
          toast({
            title: "Tag already exists",
            description:
              "A tag with this name already exists in this workspace.",
          })
        } else {
          toast({
            title: "Failed to create tag",
            description: String(error.body.detail ?? error.message),
          })
        }
      },
    }
  )

  const { mutateAsync: updateTag, isPending: updateTagIsPending } = useMutation(
    {
      mutationFn: async ({
        tagId,
        params,
      }: {
        tagId: string
        params: TagUpdate
      }) =>
        await agentTagsUpdateTag({
          tagId,
          workspaceId,
          requestBody: params,
        }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Updated tag" })
      },
      onError: (error: TracecatApiError) => {
        if (error.status === 409) {
          toast({
            title: "Tag already exists",
            description: "Another tag with this name already exists.",
          })
        } else {
          toast({
            title: "Failed to update tag",
            description: String(error.body.detail ?? error.message),
          })
        }
      },
    }
  )

  const { mutateAsync: deleteTag, isPending: deleteTagIsPending } = useMutation(
    {
      mutationFn: async (tagId: string) =>
        await agentTagsDeleteTag({ tagId, workspaceId }),
      onSuccess: () => {
        invalidate()
        toast({ title: "Deleted tag" })
      },
      onError: (error: TracecatApiError) => {
        toast({
          title: "Failed to delete tag",
          description: String(error.body.detail ?? error.message),
        })
      },
    }
  )

  return {
    tags,
    tagsIsLoading,
    tagsError,
    createTag,
    createTagIsPending,
    updateTag,
    updateTagIsPending,
    deleteTag,
    deleteTagIsPending,
  }
}

/**
 * Manage tags attached to a single agent preset.
 */
export function usePresetTags(
  presetId: string,
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true
  const queryClient = useQueryClient()

  const invalidate = () => {
    queryClient.invalidateQueries({
      queryKey: [PRESET_TAGS_KEY, workspaceId, presetId],
    })
    queryClient.invalidateQueries({ queryKey: [DIRECTORY_KEY, workspaceId] })
  }

  const {
    data: presetTags,
    isLoading: presetTagsIsLoading,
    error: presetTagsError,
  } = useQuery<TagRead[]>({
    queryKey: [PRESET_TAGS_KEY, workspaceId, presetId],
    queryFn: async () =>
      await agentPresetsListPresetTags({ presetId, workspaceId }),
    enabled: enabled && !!workspaceId && !!presetId,
  })

  const { mutateAsync: addPresetTag, isPending: addPresetTagIsPending } =
    useMutation({
      mutationFn: async (tagId: string) =>
        await agentPresetsAddPresetTag({
          presetId,
          workspaceId,
          requestBody: { tag_id: tagId },
        }),
      onSuccess: () => {
        invalidate()
      },
      onError: (error: TracecatApiError) => {
        toast({
          title: "Failed to attach tag",
          description: String(error.body.detail ?? error.message),
        })
      },
    })

  const { mutateAsync: removePresetTag, isPending: removePresetTagIsPending } =
    useMutation({
      mutationFn: async (tagId: string) =>
        await agentPresetsRemovePresetTag({ presetId, tagId, workspaceId }),
      onSuccess: () => {
        invalidate()
      },
      onError: (error: TracecatApiError) => {
        toast({
          title: "Failed to detach tag",
          description: String(error.body.detail ?? error.message),
        })
      },
    })

  return {
    presetTags,
    presetTagsIsLoading,
    presetTagsError,
    addPresetTag,
    addPresetTagIsPending,
    removePresetTag,
    removePresetTagIsPending,
  }
}
