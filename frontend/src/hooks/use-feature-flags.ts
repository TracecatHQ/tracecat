import { useQuery } from "@tanstack/react-query"

import { type FeatureFlag, featureFlagsGetFeatureFlags } from "@/client"

// Hook for components to check feature flags
export function useFeatureFlag(): {
  isFeatureEnabled: (flag: FeatureFlag) => boolean
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

  if (isLoading || error) {
    return {
      isFeatureEnabled: () => false,
    }
  }

  return {
    isFeatureEnabled: (flag: FeatureFlag) => featureFlags?.has(flag) ?? false,
  }
}
