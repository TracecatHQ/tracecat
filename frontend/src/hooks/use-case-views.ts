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

// Workspaces have few saved views; one page covers the switcher and sidebar.
const CASE_VIEWS_PAGE_SIZE = 200

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
      const page = await caseViewsListCaseViews({
        workspaceId,
        limit: CASE_VIEWS_PAGE_SIZE,
      })
      return page.items
    },
    enabled: enabled && Boolean(workspaceId),
    retry: retryHandler,
  })

  const lists = useMemo<CaseViewLists>(() => {
    const items = saved ?? []
    return {
      builtins: builtins ?? [],
      personal: items.filter((view) => view.visibility === "personal"),
      shared: items.filter((view) => view.visibility === "workspace"),
      pinned: items
        .filter((view) => view.pinned)
        .sort((a, b) => (a.pin_position ?? 0) - (b.pin_position ?? 0)),
    }
  }, [builtins, saved])

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
