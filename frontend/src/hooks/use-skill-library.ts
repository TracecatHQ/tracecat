"use client"

import {
  type LibrarySkillRead,
  type SkillRead,
  skillLibraryForkLibrarySkill,
  skillLibraryInstallLibrarySkill,
  skillLibraryListLibrarySkills,
  skillLibraryUninstallLibrarySkill,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

async function listAllLibrarySkills(
  workspaceId: string
): Promise<LibrarySkillRead[]> {
  const skills: LibrarySkillRead[] = []
  let cursor: string | undefined

  do {
    const response = await skillLibraryListLibrarySkills({
      workspaceId,
      limit: 200,
      cursor,
    })
    skills.push(...response.items)
    cursor = response.next_cursor ?? undefined
  } while (cursor)

  return skills
}

function showError(title: string, error: TracecatApiError) {
  toast({
    title,
    description: getApiErrorDetail(error) ?? "Please try again.",
    variant: "destructive",
  })
}

/**
 * List platform library skills and manage this workspace's installs.
 *
 * @param workspaceId Workspace identifier.
 * @param options Query options.
 * @returns Library query and install, uninstall, and fork mutations.
 */
export function useSkillLibrary(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()
  const libraryQuery = useQuery<LibrarySkillRead[], TracecatApiError>({
    queryKey: ["skill-library", workspaceId],
    queryFn: async () => await listAllLibrarySkills(workspaceId),
    enabled: options.enabled,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  function invalidateLibrary() {
    queryClient.invalidateQueries({ queryKey: ["skill-library", workspaceId] })
  }

  const installMutation = useMutation<
    LibrarySkillRead,
    TracecatApiError,
    string
  >({
    mutationFn: async (slug) =>
      await skillLibraryInstallLibrarySkill({ workspaceId, slug }),
    onSuccess: (skill) => {
      invalidateLibrary()
      toast({ title: "Installed skill", description: skill.slug })
    },
    onError: (error) => showError("Failed to install skill", error),
  })

  const uninstallMutation = useMutation<void, TracecatApiError, string>({
    mutationFn: async (slug) =>
      await skillLibraryUninstallLibrarySkill({ workspaceId, slug }),
    onSuccess: (_, slug) => {
      invalidateLibrary()
      toast({ title: "Uninstalled skill", description: slug })
    },
    onError: (error) => showError("Failed to uninstall skill", error),
  })

  const forkMutation = useMutation<SkillRead, TracecatApiError, string>({
    mutationFn: async (slug) =>
      await skillLibraryForkLibrarySkill({ workspaceId, slug }),
    onSuccess: (skill) => {
      queryClient.invalidateQueries({ queryKey: ["skills", workspaceId] })
      queryClient.invalidateQueries({
        queryKey: ["skill-directory-items", workspaceId],
      })
      toast({
        title: "Forked skill",
        description: `${skill.name} is now an editable workspace skill.`,
      })
    },
    onError: (error) => showError("Failed to fork skill", error),
  })

  return {
    librarySkills: libraryQuery.data,
    librarySkillsIsLoading: libraryQuery.isLoading,
    librarySkillsError: libraryQuery.error,
    installLibrarySkill: installMutation.mutateAsync,
    installLibrarySkillIsPending: installMutation.isPending,
    uninstallLibrarySkill: uninstallMutation.mutateAsync,
    uninstallLibrarySkillIsPending: uninstallMutation.isPending,
    forkLibrarySkill: forkMutation.mutateAsync,
    forkLibrarySkillIsPending: forkMutation.isPending,
  }
}
