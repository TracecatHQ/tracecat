"use client"

import {
  type IPAllowlistCheckRequest,
  type IPAllowlistCheckResult,
  type SecuritySettingsRead,
  type SecuritySettingsUpdate,
  settingsCheckIpAllowlist,
  settingsGetSecuritySettings,
  settingsUpdateSecuritySettings,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const ORG_SECURITY_SETTINGS_QUERY_KEY = ["org-security-settings"]

/**
 * Read, update, and probe the organization IP allowlist.
 */
export function useOrgSecuritySettings() {
  const queryClient = useQueryClient()

  const {
    data: securitySettings,
    isLoading: securitySettingsIsLoading,
    error: securitySettingsError,
  } = useQuery<SecuritySettingsRead, TracecatApiError>({
    queryKey: ORG_SECURITY_SETTINGS_QUERY_KEY,
    queryFn: async () => await settingsGetSecuritySettings(),
  })

  const {
    mutateAsync: updateSecuritySettings,
    isPending: updateSecuritySettingsIsPending,
  } = useMutation<void, TracecatApiError, SecuritySettingsUpdate>({
    mutationFn: async (requestBody) =>
      await settingsUpdateSecuritySettings({ requestBody }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ORG_SECURITY_SETTINGS_QUERY_KEY,
      })
      toast({
        title: "Updated IP allowlist",
        description: "Organization IP allowlist saved successfully.",
      })
    },
    onError: (error) => {
      if (error.status === 403) {
        toast({
          title: "Forbidden",
          description: "You cannot perform this action",
        })
        return
      }
      toast({
        title: "Failed to update IP allowlist",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })

  const {
    mutateAsync: checkIpAllowlist,
    isPending: checkIpAllowlistIsPending,
  } = useMutation<
    IPAllowlistCheckResult,
    TracecatApiError,
    IPAllowlistCheckRequest
  >({
    mutationFn: async (requestBody) =>
      await settingsCheckIpAllowlist({ requestBody }),
  })

  return {
    securitySettings,
    securitySettingsIsLoading,
    securitySettingsError,
    updateSecuritySettings,
    updateSecuritySettingsIsPending,
    checkIpAllowlist,
    checkIpAllowlistIsPending,
  }
}
