import {
  authGetPublicBranding,
  type BrandingSettingsUpdate,
  infoGetAppVersion,
  organizationGetOrganizationBranding,
  settingsGetBrandingSettings,
  settingsUpdateBrandingSettings,
} from "@/client"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const BRANDING_STALE_TIME_MS = 5 * 60 * 1000

/**
 * Branding for the sign-in and sign-up pages. Safe to call while signed out.
 */
export function usePublicBranding(organizationSlug: string | null) {
  const { data, isLoading } = useQuery({
    queryKey: ["public-branding", organizationSlug],
    queryFn: async () =>
      await authGetPublicBranding({ org: organizationSlug ?? undefined }),
    staleTime: BRANDING_STALE_TIME_MS,
    refetchOnWindowFocus: false,
    retry: false,
  })
  return { branding: data ?? null, brandingIsLoading: isLoading }
}

/** Branding for the signed-in user's current organization. */
export function useOrganizationBranding() {
  const { data } = useQuery({
    queryKey: ["organization-branding"],
    queryFn: async () => await organizationGetOrganizationBranding(),
    staleTime: BRANDING_STALE_TIME_MS,
    refetchOnWindowFocus: false,
    retry: false,
  })
  return { branding: data ?? null }
}

/** Platform version, visible to signed-in users only. */
export function useAppVersion({ enabled = true }: { enabled?: boolean } = {}) {
  const { data } = useQuery({
    queryKey: ["app-version"],
    queryFn: async () => await infoGetAppVersion(),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    retry: false,
  })
  return { version: data?.version ?? null }
}

/** Read and update the organization's stored branding settings. */
export function useOrgBrandingSettings() {
  const queryClient = useQueryClient()
  const {
    data: brandingSettings,
    isLoading: brandingSettingsIsLoading,
    error: brandingSettingsError,
  } = useQuery({
    queryKey: ["org-branding-settings"],
    queryFn: async () => await settingsGetBrandingSettings(),
  })
  const {
    mutateAsync: updateBrandingSettings,
    isPending: updateBrandingSettingsIsPending,
  } = useMutation({
    mutationFn: async (requestBody: BrandingSettingsUpdate) =>
      await settingsUpdateBrandingSettings({ requestBody }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["org-branding-settings"] }),
        queryClient.invalidateQueries({ queryKey: ["organization-branding"] }),
        queryClient.invalidateQueries({ queryKey: ["public-branding"] }),
      ])
    },
  })
  return {
    brandingSettings,
    brandingSettingsIsLoading,
    brandingSettingsError,
    updateBrandingSettings,
    updateBrandingSettingsIsPending,
  }
}
