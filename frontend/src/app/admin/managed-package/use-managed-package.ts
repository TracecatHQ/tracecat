"use client"

import { useEffect, useRef } from "react"
import {
  adminApplyRollouts,
  adminAssignTarget,
  adminCutRelease,
  adminGetReleaseCandidate,
  adminGetRollout,
  adminGetSource,
  adminListReferenceMatches,
  adminListRollouts,
  adminListSourceTags,
  adminListTargetCandidates,
  adminListTargets,
  adminPreviewRollouts,
  adminSaveReferenceMatches,
  adminSetSource,
  adminUnassignTarget,
  adminUpdateTarget,
  type GitTagInfo,
  type ManagedReferenceMatchItem,
  type ManagedReleaseCandidateRead,
  type ManagedRolloutRead,
  type ManagedTargetRead,
  type ManagedWorkspaceRead,
} from "@/client"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"
import {
  ACTIVE_ROLLOUT_STATUSES,
  RESUMABLE_ROLLOUT_STATUSES,
} from "./managed-package-utils"

const BASE_KEY = ["admin", "managed-package"] as const
const PAGE_LIMIT = 200
const MAX_PAGES = 50
const POLL_INTERVAL_MS = 2000
// Callers on this page render or toast failures themselves.
const SUPPRESS_TOAST = { suppressErrorToast: true }

/** Query keys for the managed package admin page. */
export const managedPackageKeys = {
  all: BASE_KEY,
  source: [...BASE_KEY, "source"] as const,
  tags: [...BASE_KEY, "tags"] as const,
  releaseCandidate: [...BASE_KEY, "release-candidate"] as const,
  targets: [...BASE_KEY, "targets"] as const,
  candidates: (organizationId: string | null) =>
    [...BASE_KEY, "candidates", organizationId] as const,
  references: (targetId: string | null) =>
    [...BASE_KEY, "references", targetId] as const,
  resumableRollouts: [...BASE_KEY, "resumable-rollouts"] as const,
  targetRollouts: (targetId: string | null) =>
    [...BASE_KEY, "target-rollouts", targetId] as const,
  rollouts: (rolloutIds: string[]) =>
    [...BASE_KEY, "rollouts", ...rolloutIds] as const,
}

/**
 * Walk a cursor-paged endpoint and return every item. Fails rather than
 * return a silently truncated list when more than `MAX_PAGES` pages exist.
 */
async function fetchAllPages<T>(
  fetchPage: (
    cursor: string | null
  ) => Promise<{ items: T[]; next_cursor?: string | null }>
): Promise<T[]> {
  const items: T[] = []
  let cursor: string | null = null
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await fetchPage(cursor)
    items.push(...result.items)
    cursor = result.next_cursor ?? null
    if (!cursor) {
      return items
    }
  }
  throw new Error(
    `More than ${MAX_PAGES * PAGE_LIMIT} items; the list cannot be shown in full.`
  )
}

function useInvalidateManagedPackage() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: BASE_KEY })
}

/** The configured source workspace and a mutation to change it. */
export function useManagedSource() {
  const invalidate = useInvalidateManagedPackage()
  const query = useQuery({
    queryKey: managedPackageKeys.source,
    meta: SUPPRESS_TOAST,
    queryFn: adminGetSource,
    retry: false,
  })
  const setSource = useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (workspaceId: string) =>
      adminSetSource({ requestBody: { workspace_id: workspaceId } }),
    onSuccess: invalidate,
  })
  return {
    source: query.data?.source ?? null,
    isLoading: query.isLoading,
    error: query.error,
    setSource: setSource.mutateAsync,
    setSourcePending: setSource.isPending,
  }
}

/** Every tag in the source repository, newest first. */
export function useManagedTags({ enabled }: { enabled: boolean }) {
  const query = useQuery<GitTagInfo[]>({
    queryKey: managedPackageKeys.tags,
    meta: SUPPRESS_TOAST,
    queryFn: () =>
      fetchAllPages((cursor) =>
        adminListSourceTags({ limit: PAGE_LIMIT, cursor })
      ),
    enabled,
    retry: false,
  })
  return {
    tags: query.data ?? [],
    isLoading: query.isLoading,
    error: query.error,
  }
}

/**
 * The commit a new release would tag (the source base branch HEAD), tags
 * already on it, and the package published in it.
 */
export function useReleaseCandidate({ enabled }: { enabled: boolean }) {
  const query = useQuery<ManagedReleaseCandidateRead>({
    queryKey: managedPackageKeys.releaseCandidate,
    meta: SUPPRESS_TOAST,
    queryFn: () => adminGetReleaseCandidate(),
    enabled,
    retry: false,
  })
  return {
    candidate: query.data,
    isLoading: query.isLoading,
    error: query.error,
  }
}

/**
 * Tag the source repository's base branch HEAD as a new release. Passing the
 * reviewed `commitSha` makes the backend refuse if the branch moved since.
 */
export function useCutRelease() {
  const invalidate = useInvalidateManagedPackage()
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (params: { tag: string; commitSha: string }) =>
      adminCutRelease({
        requestBody: { tag: params.tag, commit_sha: params.commitSha },
      }),
    onSuccess: invalidate,
  })
}

/** Every target workspace with its pin and last rollout. */
export function useManagedTargets() {
  const query = useQuery<ManagedTargetRead[]>({
    queryKey: managedPackageKeys.targets,
    meta: SUPPRESS_TOAST,
    queryFn: () =>
      fetchAllPages((cursor) =>
        adminListTargets({ limit: PAGE_LIMIT, cursor })
      ),
  })
  return {
    targets: query.data ?? [],
    isLoading: query.isLoading,
    error: query.error,
  }
}

/** Workspaces in one organization that could become targets. */
export function useTargetCandidates(organizationId: string | null) {
  const query = useQuery<ManagedWorkspaceRead[]>({
    queryKey: managedPackageKeys.candidates(organizationId),
    meta: SUPPRESS_TOAST,
    queryFn: () =>
      fetchAllPages((cursor) =>
        adminListTargetCandidates({
          organizationId,
          limit: PAGE_LIMIT,
          cursor,
        })
      ),
    enabled: organizationId !== null,
  })
  return {
    candidates: query.data ?? [],
    isLoading: query.isLoading && organizationId !== null,
    error: query.error,
  }
}

/** Assign a workspace as a target pinned to a tag. */
export function useAssignTarget() {
  const invalidate = useInvalidateManagedPackage()
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (params: { workspaceId: string; pinnedTag: string }) =>
      adminAssignTarget({
        requestBody: {
          workspace_id: params.workspaceId,
          pinned_tag: params.pinnedTag,
        },
      }),
    onSuccess: invalidate,
  })
}

/** Move a target's pinned tag without applying it. */
export function useUpdateTargetPin() {
  const invalidate = useInvalidateManagedPackage()
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (params: { targetId: string; pinnedTag: string }) =>
      adminUpdateTarget({
        targetId: params.targetId,
        requestBody: { pinned_tag: params.pinnedTag },
      }),
    onSuccess: invalidate,
  })
}

/** Start an unassign preview; the returned rollout must be applied later. */
export function useUnassignTarget() {
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (targetId: string) => adminUnassignTarget({ targetId }),
  })
}

/** Saved reference matches for one target. */
export function useReferenceMatches(targetId: string | null) {
  const query = useQuery<ManagedReferenceMatchItem[]>({
    queryKey: managedPackageKeys.references(targetId),
    meta: SUPPRESS_TOAST,
    queryFn: () => adminListReferenceMatches({ targetId: targetId ?? "" }),
    enabled: targetId !== null,
  })
  return {
    matches: query.data ?? [],
    isLoading: query.isLoading,
    error: query.error,
  }
}

/** Upsert reference matches for one target. */
export function useSaveReferenceMatches() {
  const invalidate = useInvalidateManagedPackage()
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (params: {
      targetId: string
      matches: ManagedReferenceMatchItem[]
    }) =>
      adminSaveReferenceMatches({
        targetId: params.targetId,
        requestBody: { matches: params.matches },
      }),
    onSuccess: invalidate,
  })
}

/**
 * The newest apply rollout for a target. Polls while its preview is still
 * running, and never falls back to an older rollout's stale preview.
 */
export function useLatestTargetPreview(targetId: string | null) {
  const query = useQuery<ManagedRolloutRead | null>({
    queryKey: managedPackageKeys.targetRollouts(targetId),
    queryFn: async () => {
      // Newest first.
      const page = await adminListRollouts({ targetId, limit: 20 })
      return page.items.find((rollout) => rollout.kind === "apply") ?? null
    },
    enabled: targetId !== null,
    refetchInterval: (q) =>
      q.state.data && ACTIVE_ROLLOUT_STATUSES.has(q.state.data.status)
        ? POLL_INTERVAL_MS
        : false,
  })
  const rollout = query.data ?? null
  return {
    rollout,
    previewRunning:
      rollout !== null && ACTIVE_ROLLOUT_STATUSES.has(rollout.status),
    isLoading: query.isLoading,
    error: query.error,
  }
}

/**
 * Apply rollouts saved from earlier visits that can still be followed or
 * applied: each target's newest rollout, when it is an apply rollout that is
 * running, ready, or waiting on references or conflicts. Lets the page pick
 * up previews after a reload.
 */
export function useResumableRollouts() {
  const query = useQuery<ManagedRolloutRead[]>({
    queryKey: managedPackageKeys.resumableRollouts,
    meta: SUPPRESS_TOAST,
    queryFn: async () => {
      // Newest first, so the first rollout seen for a target is its latest.
      const page = await adminListRollouts({ limit: PAGE_LIMIT })
      const newestByTarget = new Map<string, ManagedRolloutRead>()
      for (const rollout of page.items) {
        if (rollout.target_id && !newestByTarget.has(rollout.target_id)) {
          newestByTarget.set(rollout.target_id, rollout)
        }
      }
      return [...newestByTarget.values()].filter(
        (rollout) =>
          rollout.kind === "apply" &&
          RESUMABLE_ROLLOUT_STATUSES.has(rollout.status)
      )
    },
    retry: false,
  })
  return { rollouts: query.data }
}

/** Start one preview per target for a tag (null means each target's pin). */
export function usePreviewRollouts() {
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (params: { targetIds: string[]; tag: string | null }) =>
      adminPreviewRollouts({
        requestBody: { target_ids: params.targetIds, tag: params.tag },
      }),
  })
}

/** Apply the ready rollouts among the given ids. */
export function useApplyRollouts() {
  return useMutation({
    meta: SUPPRESS_TOAST,
    mutationFn: (rolloutIds: string[]) =>
      adminApplyRollouts({ requestBody: { rollout_ids: rolloutIds } }),
  })
}

/**
 * Live state of a set of rollouts. Polls every two seconds while any of them
 * is previewing or applying, and refreshes the targets table once they all
 * settle.
 */
export function useTrackedRollouts(rolloutIds: string[]) {
  const queryClient = useQueryClient()
  const query = useQuery<ManagedRolloutRead[]>({
    queryKey: managedPackageKeys.rollouts(rolloutIds),
    queryFn: () =>
      Promise.all(
        rolloutIds.map((rolloutId) => adminGetRollout({ rolloutId }))
      ),
    enabled: rolloutIds.length > 0,
    meta: SUPPRESS_TOAST,
    refetchInterval: (q) => {
      // Stop on a failed read; callers show the error and can refetch.
      if (q.state.status === "error") {
        return false
      }
      const data = q.state.data
      if (!data) {
        return POLL_INTERVAL_MS
      }
      return data.some((rollout) => ACTIVE_ROLLOUT_STATUSES.has(rollout.status))
        ? POLL_INTERVAL_MS
        : false
    },
  })
  const rollouts = query.data ?? []
  const isActive = rollouts.some((rollout) =>
    ACTIVE_ROLLOUT_STATUSES.has(rollout.status)
  )

  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && !isActive) {
      queryClient.invalidateQueries({ queryKey: managedPackageKeys.targets })
      queryClient.invalidateQueries({
        queryKey: [...BASE_KEY, "target-rollouts"],
      })
    }
    wasActive.current = isActive
  }, [isActive, queryClient])

  return {
    rollouts,
    isActive,
    error: query.error,
    refetch: query.refetch,
  }
}
