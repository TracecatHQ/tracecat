"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import type { CaseViewRead, CaseViewVisibility } from "@/client"
import { DEFAULT_CASE_SORT } from "@/components/cases/case-sort"
import { normalizeVisibleColumnIds } from "@/hooks/use-case-column-visibility"
import { useCaseView, useCaseViewMutations } from "@/hooks/use-case-views"
import type { UseCasesResult } from "@/hooks/use-cases"
import {
  type CaseViewFilters,
  columnsEqual,
  filtersToViewQuery,
  mergeViewQuery,
  sortToView,
  viewQueriesEqual,
  viewSaveBlocker,
  viewToFilters,
} from "@/lib/case-views"

/** The URL search parameter that names the open case view. */
export const VIEW_PARAM = "view"

/** The view named in the URL, loaded and split for the filter bar. */
export interface ActiveCaseView {
  /** The `?view=` value, or null when no view is open. */
  viewId: string | null
  view: CaseViewRead | null
  mapped: CaseViewFilters | null
  isLoading: boolean
  /** The view could not be loaded, for example because it was deleted. */
  isError: boolean
  selectView: (viewId: string | null) => void
}

/**
 * Read `?view=` from the URL and load that case view.
 *
 * The cases page mounts its list once per view (keyed by `viewId`) with the
 * view's filters as the starting state, so nothing here has to be applied
 * or undone later.
 */
export function useActiveCaseView(workspaceId: string): ActiveCaseView {
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const viewId = searchParams?.get(VIEW_PARAM) ?? null
  const { data, isLoading, isError } = useCaseView(workspaceId, viewId)
  const view = viewId !== null && data?.id === viewId ? data : null
  const mapped = useMemo(() => (view ? viewToFilters(view) : null), [view])

  const selectView = useCallback(
    (nextViewId: string | null) => {
      const params = new URLSearchParams(searchParams?.toString())
      if (nextViewId === null) {
        params.delete(VIEW_PARAM)
      } else {
        params.set(VIEW_PARAM, nextViewId)
      }
      const query = params.toString()
      router.push(query ? `${pathname}?${query}` : (pathname ?? ""))
    },
    [pathname, router, searchParams]
  )

  return { viewId, view, mapped, isLoading, isError, selectView }
}

/** The columns the list shows, and how to change them. */
export interface ViewColumns {
  /** Columns chosen for the open view, or null to use the user's own. */
  columns: string[] | null
  /** The normalized columns to show and fetch. */
  visibleColumnIds: string[]
  toggleColumn: (columnId: string) => void
  /** Go back to the view's saved columns. */
  resetColumns: () => void
}

/**
 * Track the visible columns for the open view, or the user's own without one.
 *
 * A view keeps its own column choice; changing it marks the view as modified
 * rather than changing the user's browser setting.
 */
export function useViewColumns({
  view,
  personalColumns,
  togglePersonalColumn,
  knownColumnIds,
}: {
  view: CaseViewRead | null
  personalColumns: string[]
  togglePersonalColumn: (columnId: string) => void
  knownColumnIds?: Set<string>
}): ViewColumns {
  const [columns, setColumns] = useState<string[] | null>(view?.columns ?? null)
  const visibleColumnIds =
    view && columns !== null
      ? normalizeVisibleColumnIds(columns, knownColumnIds)
      : personalColumns

  const toggleColumn = useCallback(
    (columnId: string) => {
      if (!view) {
        togglePersonalColumn(columnId)
        return
      }
      const base = normalizeVisibleColumnIds(
        columns ?? personalColumns,
        knownColumnIds
      )
      setColumns(
        base.includes(columnId)
          ? base.filter((id) => id !== columnId)
          : normalizeVisibleColumnIds([...base, columnId], knownColumnIds)
      )
    },
    [view, columns, personalColumns, knownColumnIds, togglePersonalColumn]
  )
  const resetColumns = useCallback(
    () => setColumns(view?.columns ?? null),
    [view]
  )

  return { columns, visibleColumnIds, toggleColumn, resetColumns }
}

/** What the cases page needs to show and change the open view. */
export interface CaseViewControls {
  active: ActiveCaseView
  /** The open view's filters, sort, or columns differ from its saved ones. */
  isModified: boolean
  save: () => Promise<void>
  saveAsNew: (name: string, visibility: CaseViewVisibility) => Promise<void>
  reset: () => void
  isSaving: boolean
  /** Why the current search cannot be saved, or null when it can. */
  saveBlockedReason: string | null
}

/** Track changes against the open view, and save, duplicate, or reset it. */
export function useCaseViewControls({
  workspaceId,
  active,
  cases,
  viewColumns,
  personalColumns,
}: {
  workspaceId: string
  active: ActiveCaseView
  cases: Pick<UseCasesResult, "filters" | "replaceFilters">
  viewColumns: ViewColumns
  personalColumns: string[]
}): CaseViewControls {
  const { createView, updateView } = useCaseViewMutations(workspaceId)
  const { view, mapped } = active
  const { replaceFilters } = cases
  const { columns, resetColumns } = viewColumns

  // The same merge builds the search request in useCases, so what a view
  // saves is exactly what the list searched.
  const currentQuery = useMemo(
    () =>
      mergeViewQuery(
        mapped?.extraQuery ?? {},
        filtersToViewQuery(cases.filters)
      ),
    [cases.filters, mapped]
  )
  const saveBlockedReason = viewSaveBlocker(cases.filters, mapped?.emptyRules)
  const currentSort = sortToView(cases.filters)

  const isModified = useMemo(() => {
    if (!view || !mapped) {
      return false
    }
    const savedOrder = view.order_by ?? DEFAULT_CASE_SORT.field
    const savedSort = view.sort ?? DEFAULT_CASE_SORT.direction
    return (
      !viewQueriesEqual(currentQuery, mapped.savedQuery) ||
      currentSort.order_by !== savedOrder ||
      currentSort.sort !== savedSort ||
      (columns !== null && !columnsEqual(columns, view.columns))
    )
  }, [view, mapped, currentQuery, currentSort, columns])

  const save = useCallback(async () => {
    if (!view || !view.can_edit || saveBlockedReason) {
      return
    }
    await updateView.mutateAsync({
      viewId: view.id,
      requestBody: {
        query: currentQuery,
        order_by: currentSort.order_by,
        sort: currentSort.sort,
        columns: columns ?? view.columns ?? null,
      },
    })
  }, [view, updateView, currentQuery, currentSort, columns, saveBlockedReason])

  const saveAsNew = useCallback(
    async (name: string, visibility: CaseViewVisibility) => {
      if (saveBlockedReason) {
        return
      }
      const created = await createView.mutateAsync({
        name,
        visibility,
        query: currentQuery,
        order_by: currentSort.order_by,
        sort: currentSort.sort,
        columns: columns ?? (view ? view.columns : personalColumns) ?? null,
      })
      active.selectView(created.id)
    },
    [
      createView,
      currentQuery,
      currentSort,
      columns,
      view,
      personalColumns,
      active,
      saveBlockedReason,
    ]
  )

  const reset = useCallback(() => {
    if (!mapped) {
      return
    }
    replaceFilters(mapped.filters)
    resetColumns()
  }, [mapped, replaceFilters, resetColumns])

  return {
    active,
    isModified,
    save,
    saveAsNew,
    reset,
    isSaving: createView.isPending || updateView.isPending,
    saveBlockedReason,
  }
}
