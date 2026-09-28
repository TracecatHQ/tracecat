"use client"

import {
  registryRepositoriesCompareRegistryVersions,
  registryRepositoriesDeleteRegistryVersion,
  registryRepositoriesListRepositoryVersions,
  registryRepositoriesPromoteRegistryVersion,
  type tracecat__registry__repositories__schemas__RegistryVersionRead,
  type VersionDiff,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

type RegistryVersionRead =
  tracecat__registry__repositories__schemas__RegistryVersionRead

/** Versions of one registry repository with promote and delete mutations. */
export function useRegistryVersions(repositoryId: string | null) {
  const queryClient = useQueryClient()
  const {
    data: versions,
    isLoading: versionsIsLoading,
    error: versionsError,
  } = useQuery<RegistryVersionRead[]>({
    queryKey: ["registry_versions", repositoryId],
    queryFn: async () => {
      if (!repositoryId) {
        throw new Error("Repository ID is required")
      }
      return await registryRepositoriesListRepositoryVersions({
        repositoryId,
      })
    },
    enabled: !!repositoryId,
  })

  function invalidateVersionQueries() {
    queryClient.invalidateQueries({ queryKey: ["registry_repositories"] })
    queryClient.invalidateQueries({
      queryKey: ["registry_versions", repositoryId],
    })
    queryClient.invalidateQueries({ queryKey: ["registry_actions"] })
  }

  function toastVersionError(title: string, error: TracecatApiError) {
    switch (error.status) {
      case 403:
        return toast({
          title: "Forbidden",
          description: "You are not authorized to perform this action",
        })
      default:
        return toast({
          title,
          description: getApiErrorDetail(error) ?? "Please try again.",
          variant: "destructive",
        })
    }
  }

  const { mutateAsync: promoteVersion, isPending: promoteVersionIsPending } =
    useMutation({
      mutationFn: async ({
        versionId,
      }: {
        versionId: string
        versionName: string
      }) => {
        if (!repositoryId) {
          throw new Error("Repository ID is required")
        }
        return await registryRepositoriesPromoteRegistryVersion({
          repositoryId,
          versionId,
        })
      },
      onSuccess: (_, { versionName }) => {
        invalidateVersionQueries()
        toast({
          title: "Version promoted",
          description: `Version ${versionName} is now active.`,
        })
      },
      onError: (error: TracecatApiError) =>
        toastVersionError("Couldn't promote version", error),
    })

  const { mutateAsync: deleteVersion, isPending: deleteVersionIsPending } =
    useMutation({
      mutationFn: async (versionId: string) => {
        if (!repositoryId) {
          throw new Error("Repository ID is required")
        }
        return await registryRepositoriesDeleteRegistryVersion({
          repositoryId,
          versionId,
        })
      },
      onSuccess: () => {
        invalidateVersionQueries()
        toast({
          title: "Version deleted",
          description: "The version has been deleted.",
        })
      },
      onError: (error: TracecatApiError) =>
        toastVersionError("Couldn't delete version", error),
    })

  return {
    versions,
    versionsIsLoading,
    versionsError,
    promoteVersion,
    promoteVersionIsPending,
    deleteVersion,
    deleteVersionIsPending,
  }
}

/** Options for {@link useRegistryVersionDiff}. */
export interface UseRegistryVersionDiffOptions {
  repositoryId: string | null
  baseId: string | null
  compareToId: string | null
  enabled?: boolean
}

/** Action-level diff between two registry versions of one repository. */
export function useRegistryVersionDiff({
  repositoryId,
  baseId,
  compareToId,
  enabled,
}: UseRegistryVersionDiffOptions) {
  const {
    data: diff,
    isLoading: diffIsLoading,
    error: diffError,
  } = useQuery<VersionDiff>({
    queryKey: ["registry_version_diff", repositoryId, baseId, compareToId],
    queryFn: async () => {
      if (!repositoryId || !baseId || !compareToId) {
        throw new Error("Both versions are required")
      }
      return await registryRepositoriesCompareRegistryVersions({
        repositoryId,
        versionId: baseId,
        compareTo: compareToId,
      })
    },
    enabled: enabled !== false && !!repositoryId && !!baseId && !!compareToId,
  })

  return {
    diff,
    diffIsLoading,
    diffError,
  }
}
