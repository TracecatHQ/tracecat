import { useQuery } from "@tanstack/react-query"

import { type FeatureFlag, featureFlagsGetFeatureFlags } from "@/client"

// Hook for components to check feature flags
export function useFeatureFlag(): {
  isFeatureEnabled: (flag: FeatureFlag) => boolean
  isLoading: boolean
  hasFeatureData: boolean
} {
  const {
    data: featureFlags,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["feature-flags"],
    queryFn: async () => {
      const response = await featureFlagsGetFeatureFlags()
      return new Set(response.enabled_features)
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
    refetchOnWindowFocus: false,
  })

  const hasFeatureData = featureFlags !== undefined

  return {
    isFeatureEnabled: (flag: FeatureFlag) => {
      // Returns false while loading or on error; components should use isLoading to handle loading state
      if (isLoading || error) return false
      return featureFlags?.has(flag) ?? false
    },
    isLoading,
    hasFeatureData,
  }
}

export async function isFeatureEnabledSS(flag: FeatureFlag): Promise<boolean> {
  // Runs server-side
  const response = await featureFlagsGetFeatureFlags()
  return response.enabled_features.includes(flag)
}
