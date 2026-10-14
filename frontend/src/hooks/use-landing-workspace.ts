import { useEffect, useState } from "react"
import { usersGetMyScopes, type WorkspaceReadMinimal } from "@/client"
import { useQuery } from "@/lib/query"
import { hasGrantedScope } from "@/lib/scopes"

const STALE_TIME_MS = 5 * 60 * 1000

/**
 * Pick the workspace a user lands in when the URL names none they can open:
 * the last-viewed one, else the first where they hold `workspace:read` (an
 * intake-only first workspace would bounce a builder to `/forms`), else the
 * first. `undefined` while that is worked out. Scopes are asked for only when
 * needed, one workspace at a time; a failed request counts as unreadable.
 */
export function useLandingWorkspace({
  workspaces,
  lastViewedWorkspaceId,
  enabled,
}: {
  workspaces: WorkspaceReadMinimal[] | undefined
  lastViewedWorkspaceId: string | undefined
  enabled: boolean
}): string | undefined {
  const [index, setIndex] = useState(0)
  const count = workspaces?.length ?? 0
  const needsSearch = enabled && !lastViewedWorkspaceId && count > 1
  const candidateId = needsSearch ? workspaces?.[index]?.id : undefined

  const { data, isError } = useQuery({
    queryKey: ["user-scopes", candidateId],
    queryFn: () => usersGetMyScopes({ workspaceId: candidateId }),
    enabled: candidateId !== undefined,
    staleTime: STALE_TIME_MS,
    meta: { suppressErrorToast: true },
  })
  // The key without a workspace is someone else's query; ignore its data.
  const scopes = candidateId !== undefined ? data?.scopes : undefined
  const canRead = scopes ? hasGrantedScope("workspace:read", scopes) : undefined
  const isUnreadable =
    candidateId !== undefined && (canRead === false || isError)

  // Keyed on `index` too: the next candidate's answer can already be cached,
  // so `isUnreadable` may stay true from one candidate to the next.
  useEffect(() => {
    if (isUnreadable) {
      setIndex(index + 1)
    }
  }, [isUnreadable, index])

  if (lastViewedWorkspaceId) {
    return lastViewedWorkspaceId
  }
  if (!needsSearch || index >= count) {
    return workspaces?.[0]?.id
  }
  return canRead === true ? candidateId : undefined
}
