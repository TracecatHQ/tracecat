"use client"

import { useQuery } from "@tanstack/react-query"
import { inboxGetPendingCount } from "@/client"

export function usePendingApprovalsCount(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  return useQuery({
    queryKey: ["pending-approvals-count", workspaceId],
    queryFn: () => inboxGetPendingCount({ workspaceId }),
    select: (data) => data.count,
    enabled: enabled && Boolean(workspaceId),
    staleTime: 10_000,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => {
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        return false
      }

      return (query.state.data?.count ?? 0) > 0 ? 5_000 : 15_000
    },
  })
}
