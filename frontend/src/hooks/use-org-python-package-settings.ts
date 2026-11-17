"use client"

import {
  type PythonPackageSettingsRead,
  type SettingsUpdatePythonPackageSettingsData,
  settingsGetPythonPackageSettings,
  settingsUpdatePythonPackageSettings,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const QUERY_KEY = ["org-python-package-settings"]

/** Load and update the organization's Python package index settings. */
export function useOrgPythonPackageSettings() {
  const queryClient = useQueryClient()
  const {
    data: pythonPackageSettings,
    isLoading: pythonPackageSettingsIsLoading,
    error: pythonPackageSettingsError,
  } = useQuery<PythonPackageSettingsRead>({
    queryKey: QUERY_KEY,
    queryFn: async () => await settingsGetPythonPackageSettings(),
  })

  const {
    mutateAsync: updatePythonPackageSettings,
    isPending: updatePythonPackageSettingsIsPending,
  } = useMutation({
    mutationFn: async (params: SettingsUpdatePythonPackageSettingsData) =>
      await settingsUpdatePythonPackageSettings(params),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      toast({
        title: "Updated Python packages",
        description: "Python package index settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      if (error.status === 403) {
        toast({
          title: "Forbidden",
          description: "You cannot perform this action",
        })
        return
      }
      toast({
        title: "Failed to update Python packages",
        description:
          getApiErrorDetail(error) ??
          "An error occurred while updating Python package index settings.",
      })
    },
  })

  return {
    pythonPackageSettings,
    pythonPackageSettingsIsLoading,
    pythonPackageSettingsError,
    updatePythonPackageSettings,
    updatePythonPackageSettingsIsPending,
  }
}
