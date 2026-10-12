import {
  usersGetMyScopes,
  type WorkspaceReadMinimal,
  workspacesListWorkspaces,
} from "@/client"
import { useQueries, useQuery } from "@/lib/query"
import { hasGrantedScope } from "@/lib/scopes"

const STALE_TIME_MS = 5 * 60 * 1000

/**
 * The workspaces whose shell the user can open: those where they hold
 * `workspace:read`.
 *
 * A member can be intake-only in one workspace and a builder in another, and
 * the workspace list does not say which. This asks for the user's scopes in
 * each workspace, one request per workspace, so mount it only when the
 * answer is needed. The query keys match `useWorkspaceManager` and
 * `useUserScopes`, so answers are shared with the workspace pages.
 */
export function useReadableWorkspaces(): WorkspaceReadMinimal[] {
  const { data: workspaces } = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => workspacesListWorkspaces(),
    staleTime: STALE_TIME_MS,
    meta: { suppressErrorToast: true },
  })
  const scopes = useQueries({
    queries: (workspaces ?? []).map((workspace) => ({
      queryKey: ["user-scopes", workspace.id],
      queryFn: () => usersGetMyScopes({ workspaceId: workspace.id }),
      staleTime: STALE_TIME_MS,
      meta: { suppressErrorToast: true },
    })),
  })
  return (workspaces ?? []).filter((_workspace, index) =>
    hasGrantedScope("workspace:read", scopes[index]?.data?.scopes ?? [])
  )
}
