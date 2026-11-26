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
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

// The API caps a page at 200. A workspace with more triggers or sources than
// that is not expected in v1; the list says so rather than paging.
const PAGE_LIMIT = 200

function triggersQueryKey(workspaceId: string) {
  return ["workspace-triggers", workspaceId] as const
}

function eventSourcesQueryKey(workspaceId: string) {
  return ["workspace-event-sources", workspaceId] as const
}

/** Lists a workspace's triggers, each with its config and targets. */
export function useTriggers(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const { data, isLoading, error } = useQuery<
    Awaited<ReturnType<typeof triggersListTriggers>>,
    TracecatApiError
  >({
    queryKey: triggersQueryKey(workspaceId),
    queryFn: async () =>
      await triggersListTriggers({ workspaceId, limit: PAGE_LIMIT }),
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })
  return {
    triggers: data?.items ?? [],
    hasMore: data?.has_more ?? false,
    isLoading,
    error,
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

/** Lists a workspace's event sources. */
export function useEventSources(
  workspaceId: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const { data, isLoading, error } = useQuery<
    Awaited<ReturnType<typeof triggersListEventSources>>,
    TracecatApiError
  >({
    queryKey: eventSourcesQueryKey(workspaceId),
    queryFn: async () =>
      await triggersListEventSources({ workspaceId, limit: PAGE_LIMIT }),
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })
  return { sources: data?.items ?? [], isLoading, error }
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
    return queryClient.invalidateQueries({
      queryKey: triggersQueryKey(workspaceId),
    })
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
