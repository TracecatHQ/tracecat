import { useEffect, useState } from "react"
import { usersGetMyScopes, type WorkspaceReadMinimal } from "@/client"
import { useQuery } from "@/lib/query"
import { hasGrantedScope } from "@/lib/scopes"

const STALE_TIME_MS = 5 * 60 * 1000

/** Result of {@link useLandingWorkspace}. */
export interface LandingWorkspace {
  /** The workspace to land in. `undefined` while it is being worked out. */
  workspaceId: string | undefined
}

/**
 * Pick the workspace a user lands in when the URL names none they can open.
 *
 * The last-viewed workspace wins, then the first workspace where the user
 * holds `workspace:read`. A member can be intake-only in the first-listed
 * workspace and a builder in another; landing them in the first would send
 * them to `/forms` on every visit.
 *
 * Asks for scopes only when the answer is needed (`enabled`, no last-viewed
 * workspace, more than one workspace), one workspace at a time in list order,
 * and stops at the first one the user can read. The query key matches
 * `useUserScopes`, and the workspace shell loads the landing workspace's
 * scopes anyway, so a user who can read the first workspace costs no extra
 * request. A failed request counts as "cannot read" and the search moves on.
 * With no readable workspace the first one is returned, as before.
 */
export function useLandingWorkspace({
  workspaces,
  lastViewedWorkspaceId,
  enabled,
}: {
  workspaces: WorkspaceReadMinimal[] | undefined
  lastViewedWorkspaceId: string | undefined
  enabled: boolean
}): LandingWorkspace {
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
    return { workspaceId: lastViewedWorkspaceId }
  }
  if (!needsSearch || index >= count) {
    return { workspaceId: workspaces?.[0]?.id }
  }
  return { workspaceId: canRead === true ? candidateId : undefined }
}
