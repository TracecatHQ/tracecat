import type { QueryClient } from "@tanstack/react-query"

/**
 * Invalidate the common set of case queries that should refresh
 * after any case-mutating action (events, durations, case detail).
 *
 * Individual hooks should still invalidate their domain-specific keys
 * (e.g., "case-tasks", "case-tags") in addition to calling this.
 */
export function invalidateCaseActivityQueries(
  queryClient: QueryClient,
  caseId: string,
  workspaceId: string
) {
  queryClient.invalidateQueries({ queryKey: ["case", caseId] })
  queryClient.invalidateQueries({
    queryKey: ["case-events", caseId, workspaceId],
  })
  queryClient.invalidateQueries({
    queryKey: ["case-durations", caseId, workspaceId],
  })
}
