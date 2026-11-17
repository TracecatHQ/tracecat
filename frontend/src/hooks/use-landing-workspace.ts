import { useEffect, useMemo, useState } from "react"
import { usersGetMyScopes, type WorkspaceReadMinimal } from "@/client"
import { useQuery } from "@/lib/query"
import { hasGrantedScope } from "@/lib/scopes"

const STALE_TIME_MS = 5 * 60 * 1000

/**
 * Pick the workspace a user lands in when the URL names none they can open:
 * the first where they hold `workspace:read`, trying the last-viewed one
 * first (an intake-only workspace would bounce a builder to `/forms`, and
 * access can change after a visit), else the last-viewed or first one.
 * `undefined` while that is worked out. Scopes are asked for one workspace at
 * a time; a failed request counts as unreadable.
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
  const candidates = useMemo(() => {
    const ids = (workspaces ?? []).map((workspace) => workspace.id)
    return lastViewedWorkspaceId
      ? [
          lastViewedWorkspaceId,
          ...ids.filter((id) => id !== lastViewedWorkspaceId),
        ]
      : ids
  }, [workspaces, lastViewedWorkspaceId])
  // A refreshed list starts the scan over: the index belongs to the old one.
  const candidateKey = candidates.join("\n")
  const [scannedKey, setScannedKey] = useState(candidateKey)
  if (scannedKey !== candidateKey) {
    setScannedKey(candidateKey)
    setIndex(0)
  }
  const count = candidates.length
  const needsSearch = enabled && count > 1
  const candidateId = needsSearch ? candidates[index] : undefined

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

  if (!needsSearch || index >= count) {
    return candidates[0]
  }
  return canRead === true ? candidateId : undefined
}
