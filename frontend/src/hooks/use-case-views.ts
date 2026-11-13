"use client"

import { useMemo } from "react"
import {
  type CaseViewCreate,
  type CaseViewRead,
  type CaseViewUpdate,
  caseViewsCreateCaseView,
  caseViewsDeleteCaseView,
  caseViewsGetCaseView,
  caseViewsListBuiltinCaseViews,
  caseViewsListCaseViews,
  caseViewsPinCaseView,
  caseViewsUnpinCaseView,
  caseViewsUpdateCaseView,
} from "@/client"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const CASE_VIEWS_PAGE_SIZE = 200
// A safety stop for the page loop; 50 pages is 10,000 saved views.
const CASE_VIEWS_MAX_PAGES = 50

function caseViewsKey(workspaceId: string) {
  return ["case-views", workspaceId] as const
}

/** The case views a user can open, grouped the way the switcher lists them. */
export interface CaseViewLists {
  builtins: CaseViewRead[]
  /** The requesting user's personal views. */
  personal: CaseViewRead[]
  /** Views shared with the workspace. */
  shared: CaseViewRead[]
  /** The user's pinned views, in pin order. */
  pinned: CaseViewRead[]
}

/**
 * Load built-in and saved case views for the workspace.
 *
 * @param enabled - Gate the request, for example on `case:read`.
 */
export function useCaseViews(workspaceId: string, enabled = true) {
  const { data: builtins, isLoading: builtinsLoading } = useQuery<
    CaseViewRead[],
    TracecatApiError
  >({
    queryKey: [...caseViewsKey(workspaceId), "builtins"],
    queryFn: () => caseViewsListBuiltinCaseViews({ workspaceId }),
    enabled: enabled && Boolean(workspaceId),
    staleTime: Number.POSITIVE_INFINITY,
    retry: retryHandler,
  })
  const { data: saved, isLoading: savedLoading } = useQuery<
    CaseViewRead[],
    TracecatApiError
  >({
    queryKey: [...caseViewsKey(workspaceId), "saved"],
    queryFn: async () => {
      // The switcher and sidebar need every view the user can see, so follow
      // the cursor through all pages.
      const items: CaseViewRead[] = []
      let cursor: string | undefined
      for (let page = 0; page < CASE_VIEWS_MAX_PAGES; page++) {
        const response = await caseViewsListCaseViews({
          workspaceId,
          limit: CASE_VIEWS_PAGE_SIZE,
          cursor,
        })
        items.push(...response.items)
        if (!response.has_more || !response.next_cursor) {
          break
        }
        cursor = response.next_cursor
      }
      return items
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })

  const lists = useMemo<CaseViewLists>(() => {
    // Hide anything cached once the user can no longer read cases.
    const items = enabled ? (saved ?? []) : []
    return {
      builtins: enabled ? (builtins ?? []) : [],
      personal: items.filter((view) => view.visibility === "personal"),
      shared: items.filter((view) => view.visibility === "workspace"),
      pinned: items
        .filter((view) => view.pinned)
        .sort((a, b) => (a.pin_position ?? 0) - (b.pin_position ?? 0)),
    }
  }, [builtins, saved, enabled])

  return { ...lists, isLoading: builtinsLoading || savedLoading }
}

/**
 * Load one case view, including any tags or options it names that were deleted.
 *
 * @param viewId - A saved view's UUID or a built-in view ID; null loads nothing.
 */
export function useCaseView(workspaceId: string, viewId: string | null) {
  return useQuery<CaseViewRead, TracecatApiError>({
    queryKey: [...caseViewsKey(workspaceId), "view", viewId],
    queryFn: () => caseViewsGetCaseView({ workspaceId, viewId: viewId ?? "" }),
    enabled: Boolean(workspaceId) && viewId !== null,
    retry: retryHandler,
    meta: { suppressErrorToast: true },
  })
}

/** Create, change, delete, pin, and unpin case views. */
export function useCaseViewMutations(workspaceId: string) {
  const queryClient = useQueryClient()
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: caseViewsKey(workspaceId) })

  const createView = useMutation({
    mutationFn: (requestBody: CaseViewCreate) =>
      caseViewsCreateCaseView({ workspaceId, requestBody }),
    onSuccess: invalidate,
  })
  const updateView = useMutation({
    mutationFn: ({
      viewId,
      requestBody,
    }: {
      viewId: string
      requestBody: CaseViewUpdate
    }) => caseViewsUpdateCaseView({ workspaceId, viewId, requestBody }),
    onSuccess: invalidate,
  })
  const deleteView = useMutation({
    mutationFn: (viewId: string) =>
      caseViewsDeleteCaseView({ workspaceId, viewId }),
    onSuccess: invalidate,
  })
  const pinView = useMutation({
    mutationFn: (viewId: string) =>
      caseViewsPinCaseView({ workspaceId, viewId, requestBody: {} }),
    onSuccess: invalidate,
  })
  const unpinView = useMutation({
    mutationFn: (viewId: string) =>
      caseViewsUnpinCaseView({ workspaceId, viewId }),
    onSuccess: invalidate,
  })

  return { createView, updateView, deleteView, pinView, unpinView }
}
