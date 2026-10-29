"use client"

import { useCallback, useMemo } from "react"
import {
  type DetachedCopy,
  type DetachedCopyRead,
  type ManagedResourceDetach,
  type ManagedResourceRead,
  managedResourcesDetachManagedResource,
  managedResourcesListDetachedCopies,
  managedResourcesListManagedResources,
  type SyncResourceType,
} from "@/client"
import type { TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

/** Largest page the managed resources endpoints accept. */
const MANAGED_PAGE_SIZE = 200
const MANAGED_STALE_TIME_MS = 60_000
const NO_MANAGED: ManagedResourceRead[] = []
const NO_DETACHED: DetachedCopyRead[] = []

/** Query keys for managed resource lookups. */
export const managedResourceKeys = {
  list: (workspaceId: string) => ["managed-resources", workspaceId] as const,
  detached: (workspaceId: string) =>
    ["managed-resources-detached", workspaceId] as const,
}

/**
 * Root query keys of each resource type's lists. A detach creates a new
 * resource, so these are refetched to show the copy.
 */
const RESOURCE_LIST_KEYS: Record<SyncResourceType, string[][]> = {
  workflow: [["workflows"], ["directory-items"]],
  agent_preset: [["agent-presets"]],
  skill: [["skills"]],
  table: [["tables"]],
  case_tag: [["case-tags"]],
  case_field: [["case-fields"]],
  case_dropdown: [["case-dropdown-definitions"]],
  case_duration: [["case-duration-definitions"], ["case-durations"]],
  variable: [["workspace-variables"]],
  secret_metadata: [["workspace-secrets"]],
}

/**
 * Build the lookup key for one resource.
 *
 * Case fields have no row id of their own (their `local_id` is derived from
 * the field name), so they are keyed by field name, which is `source_id`.
 */
function lookupKey(resourceType: SyncResourceType, id: string): string {
  return `${resourceType}:${id}`
}

function managedLookupKey(resource: ManagedResourceRead): string {
  const id =
    resource.resource_type === "case_field"
      ? resource.source_id
      : resource.local_id
  return lookupKey(resource.resource_type, id)
}

/** Lookup helpers for the current workspace's managed resources. */
export interface ManagedResourcesState {
  managedResources: ManagedResourceRead[]
  detachedCopies: DetachedCopyRead[]
  isLoading: boolean
  /**
   * Whether a resource is managed by Tracecat. Pass the resource id, or the
   * field name for `case_field`.
   */
  isManaged: (resourceType: SyncResourceType, id?: string | null) => boolean
  /** The managed record for a resource, if it is managed. */
  getManaged: (
    resourceType: SyncResourceType,
    id?: string | null
  ) => ManagedResourceRead | undefined
  /** The detach record for a resource, if it is a detached copy. */
  getDetachedCopy: (
    resourceType: SyncResourceType,
    id?: string | null
  ) => DetachedCopyRead | undefined
}

/**
 * Load every managed resource and detached copy in a workspace once, and
 * expose lookups used to badge and lock managed resources.
 *
 * Failures are silent: without this data the UI stays editable and the
 * backend still refuses changes to managed resources.
 *
 * @param workspaceIdOverride Workspace to read; defaults to the route's.
 * @example
 * const { isManaged } = useManagedResources()
 * const readOnly = isManaged("workflow", workflowId)
 */
export function useManagedResources(
  workspaceIdOverride?: string
): ManagedResourcesState {
  const routeWorkspaceId = useOptionalWorkspaceId()
  const workspaceId = workspaceIdOverride ?? routeWorkspaceId

  const managedQuery = useQuery<ManagedResourceRead[], TracecatApiError>({
    queryKey: managedResourceKeys.list(workspaceId ?? ""),
    queryFn: async () => {
      if (!workspaceId) {
        return []
      }
      const items: ManagedResourceRead[] = []
      let cursor: string | null = null
      do {
        const page = await managedResourcesListManagedResources({
          workspaceId,
          limit: MANAGED_PAGE_SIZE,
          cursor,
        })
        items.push(...page.items)
        cursor = page.next_cursor ?? null
      } while (cursor)
      return items
    },
    enabled: Boolean(workspaceId),
    staleTime: MANAGED_STALE_TIME_MS,
    retry: false,
    meta: { suppressErrorToast: true },
  })

  const detachedQuery = useQuery<DetachedCopyRead[], TracecatApiError>({
    queryKey: managedResourceKeys.detached(workspaceId ?? ""),
    queryFn: async () => {
      if (!workspaceId) {
        return []
      }
      const items: DetachedCopyRead[] = []
      let cursor: string | null = null
      do {
        const page = await managedResourcesListDetachedCopies({
          workspaceId,
          limit: MANAGED_PAGE_SIZE,
          cursor,
        })
        items.push(...page.items)
        cursor = page.next_cursor ?? null
      } while (cursor)
      return items
    },
    enabled: Boolean(workspaceId),
    staleTime: MANAGED_STALE_TIME_MS,
    retry: false,
    meta: { suppressErrorToast: true },
  })

  const managedResources = managedQuery.data ?? NO_MANAGED
  const detachedCopies = detachedQuery.data ?? NO_DETACHED

  const managedByKey = useMemo(
    () =>
      new Map(managedResources.map((item) => [managedLookupKey(item), item])),
    [managedResources]
  )
  const detachedByKey = useMemo(
    () =>
      new Map(
        detachedCopies.map((item) => [
          lookupKey(item.resource_type, item.local_id),
          item,
        ])
      ),
    [detachedCopies]
  )

  const getManaged = useCallback(
    (resourceType: SyncResourceType, id?: string | null) =>
      id ? managedByKey.get(lookupKey(resourceType, id)) : undefined,
    [managedByKey]
  )
  const isManaged = useCallback(
    (resourceType: SyncResourceType, id?: string | null) =>
      getManaged(resourceType, id) !== undefined,
    [getManaged]
  )
  const getDetachedCopy = useCallback(
    (resourceType: SyncResourceType, id?: string | null) =>
      id ? detachedByKey.get(lookupKey(resourceType, id)) : undefined,
    [detachedByKey]
  )

  return {
    managedResources,
    detachedCopies,
    isLoading: managedQuery.isLoading,
    isManaged,
    getManaged,
    getDetachedCopy,
  }
}

/**
 * Detach a managed resource into an editable, customer-owned copy.
 *
 * Errors are left to the caller, which shows them inline.
 *
 * @param workspaceId Workspace that owns the resource.
 */
export function useDetachManagedResource(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    DetachedCopy,
    TracecatApiError,
    ManagedResourceDetach
  >({
    mutationFn: async (requestBody) =>
      await managedResourcesDetachManagedResource({ workspaceId, requestBody }),
    onSuccess: async (copy) => {
      const keys = [
        managedResourceKeys.list(workspaceId),
        managedResourceKeys.detached(workspaceId),
        ...RESOURCE_LIST_KEYS[copy.resource_type],
      ]
      await Promise.all(
        keys.map((queryKey) => queryClient.invalidateQueries({ queryKey }))
      )
    },
    meta: { suppressErrorToast: true },
  })
  return {
    detachResource: mutation.mutateAsync,
    detachPending: mutation.isPending,
    detachError: mutation.error,
    resetDetach: mutation.reset,
  }
}
