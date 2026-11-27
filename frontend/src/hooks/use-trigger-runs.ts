"use client"

import {
  type TriggerRunOrigin,
  type TriggerRunRead,
  type TriggerStoppedEventRead,
  triggersGetTriggerRunOrigin,
  triggersListTriggerRuns,
  triggersListTriggerStoppedEvents,
} from "@/client"
import { triggersQueryKey } from "@/hooks/use-triggers"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useInfiniteQuery, useQuery } from "@/lib/query"

const RUNS_PAGE_SIZE = 20
const NO_ORIGIN_RETRY_MS = 15_000
const NO_ORIGIN_RETRIES = 4

/** A paged list the trigger page shows, with its load-more controls. */
export interface TriggerPagedList<T> {
  items: T[]
  isLoading: boolean
  error: TracecatApiError | null
  hasNextPage: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => void
}

/**
 * Lists the runs a trigger started (workflow runs and agent turns), open runs
 * first, one page at a time.
 */
export function useTriggerRuns(
  workspaceId: string,
  triggerId: string,
  { enabled = true }: { enabled?: boolean } = {}
): TriggerPagedList<TriggerRunRead> {
  const query = useInfiniteQuery({
    queryKey: [...triggersQueryKey(workspaceId), triggerId, "runs"],
    queryFn: async ({ pageParam }) =>
      await triggersListTriggerRuns({
        workspaceId,
        triggerId,
        limit: RUNS_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled: enabled && Boolean(workspaceId && triggerId),
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    isLoading: query.isLoading,
    error: query.error as TracecatApiError | null,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: () => void query.fetchNextPage(),
  }
}

/** Lists a trigger's events that stopped retrying, newest first. */
export function useTriggerStoppedEvents(
  workspaceId: string,
  triggerId: string,
  { enabled = true }: { enabled?: boolean } = {}
): TriggerPagedList<TriggerStoppedEventRead> {
  const query = useInfiniteQuery({
    queryKey: [...triggersQueryKey(workspaceId), triggerId, "stopped-events"],
    queryFn: async ({ pageParam }) =>
      await triggersListTriggerStoppedEvents({
        workspaceId,
        triggerId,
        limit: RUNS_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled: enabled && Boolean(workspaceId && triggerId),
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    isLoading: query.isLoading,
    error: query.error as TracecatApiError | null,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: () => void query.fetchNextPage(),
  }
}

/** What a "Started by" link reads: a workflow run or an agent session. */
export type TriggerRunTarget =
  | { wfExecId: string; sessionId?: undefined }
  | { sessionId: string; wfExecId?: undefined }

/**
 * Reads the trigger that started a workflow run or an agent session; null
 * when no trigger did.
 */
export function useTriggerRunOrigin(
  workspaceId: string,
  target: TriggerRunTarget,
  { enabled = true }: { enabled?: boolean } = {}
) {
  return useQuery<TriggerRunOrigin | null, TracecatApiError>({
    queryKey: [
      ...triggersQueryKey(workspaceId),
      "origin",
      target.wfExecId ?? null,
      target.sessionId ?? null,
    ],
    queryFn: async () =>
      await triggersGetTriggerRunOrigin({
        workspaceId,
        wfExecId: target.wfExecId,
        sessionId: target.sessionId,
      }),
    enabled:
      enabled &&
      Boolean(workspaceId) &&
      Boolean(target.wfExecId || target.sessionId),
    // A found trigger never changes. None found may be visibility lag (a
    // session's first turn not indexed yet), so ask again a few times.
    staleTime: (query) =>
      query.state.data ? Number.POSITIVE_INFINITY : NO_ORIGIN_RETRY_MS,
    refetchInterval: (query) =>
      query.state.data === null &&
      query.state.dataUpdateCount <= NO_ORIGIN_RETRIES
        ? NO_ORIGIN_RETRY_MS
        : false,
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
}
