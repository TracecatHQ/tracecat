import {
  type ApiError,
  type WorkspacePackageRead,
  workflowsGetWorkspaceSyncPackage,
} from "@/client"
import { useQuery } from "@/lib/query"

/** Query key root for the managed resources package; invalidated after pushes. */
export const WORKSPACE_SYNC_PACKAGE_QUERY_KEY = "workspace-sync-package"

/**
 * Reads the managed resources package for a workspace: whether it is the
 * package source and, if so, every resource's package status under the
 * include file at `ref` (the repository default branch when omitted).
 *
 * A workspace that is not the source gets `is_source: false` and nothing
 * else, so callers keep their ordinary push flow.
 */
export function useWorkspacePackage(
  workspaceId: string,
  { ref, enabled = true }: { ref?: string; enabled?: boolean }
) {
  const { data, isLoading, isFetching, error } = useQuery<
    WorkspacePackageRead,
    ApiError
  >({
    queryKey: [WORKSPACE_SYNC_PACKAGE_QUERY_KEY, workspaceId, ref ?? null],
    queryFn: () =>
      workflowsGetWorkspaceSyncPackage({ workspaceId, ref: ref ?? null }),
    enabled: Boolean(workspaceId) && enabled,
    staleTime: 60 * 1000,
    meta: { suppressErrorToast: true },
  })
  return {
    workspacePackage: data,
    // Tests and callers may stub `useQuery`, so check the shape, not truthiness.
    isSource: data?.is_source === true,
    workspacePackageIsLoading: isLoading,
    /** True during background refetches too, e.g. after a pull or push. */
    workspacePackageIsFetching: isFetching,
    workspacePackageError: error,
  }
}
