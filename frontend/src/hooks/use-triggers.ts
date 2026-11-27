"use client"

import {
  type EventSourceCreate,
  type EventSourceRead,
  type EventSourceUpdate,
  type TriggerBindingCreate,
  type TriggerBindingRead,
  type TriggerBindingUpdate,
  type TriggerCreate,
  type TriggerRead,
  type TriggerUpdate,
  triggersCreateEventSource,
  triggersCreateTrigger,
  triggersCreateTriggerBinding,
  triggersDeleteTrigger,
  triggersDeleteTriggerBinding,
  triggersGetEventSource,
  triggersGetTrigger,
  triggersListEventSources,
  triggersListTriggers,
  triggersUpdateEventSource,
  triggersUpdateTrigger,
  triggersUpdateTriggerBinding,
} from "@/client"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const TRIGGERS_PAGE_SIZE = 50
// The API's page cap. Sources are one per SNS topic, so a workspace has few.
const SOURCES_PAGE_SIZE = 200

/** The query key prefix of every trigger read in a workspace. */
export function triggersQueryKey(workspaceId: string) {
  return ["workspace-triggers", workspaceId] as const
}

function eventSourcesQueryKey(workspaceId: string) {
  return ["workspace-event-sources", workspaceId] as const
}

/**
 * Lists a workspace's triggers newest first, each with its config and
 * targets, one page at a time.
 */
export function useTriggers(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const query = useInfiniteQuery({
    queryKey: [...triggersQueryKey(workspaceId), "list"],
    queryFn: async ({ pageParam }) =>
      await triggersListTriggers({
        workspaceId,
        limit: TRIGGERS_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
  return {
    triggers: query.data?.pages.flatMap((page) => page.items) ?? [],
    // Triggers the plan hides, counted per kind. Every page counts the same
    // filters, so the first page's count holds for the whole list.
    locked: query.data?.pages[0]?.locked ?? [],
    isLoading: query.isLoading,
    error: query.error as TracecatApiError | null,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
  }
}

/** Reads one trigger with its config and targets. */
export function useTrigger(
  workspaceId: string,
  triggerId: string | undefined,
  { enabled = true }: { enabled?: boolean } = {}
) {
  return useQuery<TriggerRead, TracecatApiError>({
    queryKey: [...triggersQueryKey(workspaceId), triggerId],
    queryFn: async () =>
      await triggersGetTrigger({ workspaceId, triggerId: triggerId ?? "" }),
    enabled: enabled && Boolean(workspaceId && triggerId),
    retry: retryHandler,
  })
}

/**
 * Lists every event source in a workspace, reading all pages: a picker that
 * missed one would leave its topic unusable (one source per topic).
 */
export function useEventSources(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const { data, isLoading, error } = useQuery<
    EventSourceRead[],
    TracecatApiError
  >({
    queryKey: [...eventSourcesQueryKey(workspaceId), "all"],
    queryFn: async () => {
      const sources: EventSourceRead[] = []
      let cursor: string | null = null
      do {
        const page = await triggersListEventSources({
          workspaceId,
          limit: SOURCES_PAGE_SIZE,
          cursor,
        })
        sources.push(...page.items)
        cursor = page.has_more ? (page.next_cursor ?? null) : null
      } while (cursor)
      return sources
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })
  return { sources: data ?? [], isLoading, error }
}

/** Reads one event source, with its endpoint URL and subscription state. */
export function useEventSource(
  workspaceId: string,
  sourceId: string | undefined,
  { enabled = true }: { enabled?: boolean } = {}
) {
  return useQuery<EventSourceRead, TracecatApiError>({
    queryKey: [...eventSourcesQueryKey(workspaceId), sourceId],
    queryFn: async () =>
      await triggersGetEventSource({ workspaceId, sourceId: sourceId ?? "" }),
    enabled: enabled && Boolean(workspaceId && sourceId),
    retry: retryHandler,
  })
}

/**
 * Mutations for triggers, their targets and event sources.
 *
 * Callers own the error feedback (`mutateAsync` in a try/catch), because the
 * shared pipeline swallows 403s and these pages must explain a missing scope.
 */
export function useTriggerMutations(workspaceId: string) {
  const queryClient = useQueryClient()
  const meta = { suppressErrorToast: true }

  function invalidateTriggers() {
    return Promise.all([
      queryClient.invalidateQueries({
        queryKey: triggersQueryKey(workspaceId),
      }),
      // A schedule trigger is a workflow schedule: the workflow page's
      // Schedules panel (`useSchedules`) shows the same rows.
      queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[1] === "schedules",
      }),
      // A webhook trigger is a workflow's webhook: the workflow page reads it
      // from the workflow (`["workflow", id]`).
      queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] === "workflow",
      }),
      // A case trigger trigger is a workflow's case trigger, which the
      // workflow page reads on its own (`["case-trigger", ...]`).
      queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] === "case-trigger",
      }),
    ])
  }

  function invalidateSources() {
    return Promise.all([
      queryClient.invalidateQueries({
        queryKey: eventSourcesQueryKey(workspaceId),
      }),
      // Trigger reads carry the source name.
      invalidateTriggers(),
    ])
  }

  const createTrigger = useMutation<
    TriggerRead,
    TracecatApiError,
    TriggerCreate
  >({
    mutationFn: async (requestBody) =>
      await triggersCreateTrigger({ workspaceId, requestBody }),
    onSuccess: invalidateTriggers,
    meta,
  })

  const updateTrigger = useMutation<
    TriggerRead,
    TracecatApiError,
    { triggerId: string; requestBody: TriggerUpdate }
  >({
    mutationFn: async ({ triggerId, requestBody }) =>
      await triggersUpdateTrigger({ workspaceId, triggerId, requestBody }),
    onSuccess: invalidateTriggers,
    meta,
  })

  const deleteTrigger = useMutation<void, TracecatApiError, string>({
    mutationFn: async (triggerId) =>
      await triggersDeleteTrigger({ workspaceId, triggerId }),
    onSuccess: invalidateTriggers,
    meta,
  })

  const createBinding = useMutation<
    TriggerBindingRead,
    TracecatApiError,
    { triggerId: string; requestBody: TriggerBindingCreate }
  >({
    mutationFn: async ({ triggerId, requestBody }) =>
      await triggersCreateTriggerBinding({
        workspaceId,
        triggerId,
        requestBody,
      }),
    onSuccess: invalidateTriggers,
    meta,
  })

  const updateBinding = useMutation<
    TriggerBindingRead,
    TracecatApiError,
    { triggerId: string; bindingId: string; requestBody: TriggerBindingUpdate }
  >({
    mutationFn: async ({ triggerId, bindingId, requestBody }) =>
      await triggersUpdateTriggerBinding({
        workspaceId,
        triggerId,
        bindingId,
        requestBody,
      }),
    onSuccess: invalidateTriggers,
    meta,
  })

  const deleteBinding = useMutation<
    void,
    TracecatApiError,
    { triggerId: string; bindingId: string }
  >({
    mutationFn: async ({ triggerId, bindingId }) =>
      await triggersDeleteTriggerBinding({ workspaceId, triggerId, bindingId }),
    onSuccess: invalidateTriggers,
    meta,
  })

  const createEventSource = useMutation<
    EventSourceRead,
    TracecatApiError,
    EventSourceCreate
  >({
    mutationFn: async (requestBody) =>
      await triggersCreateEventSource({ workspaceId, requestBody }),
    onSuccess: invalidateSources,
    meta,
  })

  const updateEventSource = useMutation<
    EventSourceRead,
    TracecatApiError,
    { sourceId: string; requestBody: EventSourceUpdate }
  >({
    mutationFn: async ({ sourceId, requestBody }) =>
      await triggersUpdateEventSource({ workspaceId, sourceId, requestBody }),
    onSuccess: invalidateSources,
    meta,
  })

  return {
    createTrigger,
    updateTrigger,
    deleteTrigger,
    createBinding,
    updateBinding,
    deleteBinding,
    createEventSource,
    updateEventSource,
  }
}
