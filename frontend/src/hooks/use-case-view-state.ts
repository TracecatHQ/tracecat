"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { CaseViewRead, CaseViewVisibility } from "@/client"
import { DEFAULT_CASE_SORT } from "@/components/cases/case-sort"
import { normalizeVisibleColumnIds } from "@/hooks/use-case-column-visibility"
import { useCaseView, useCaseViewMutations } from "@/hooks/use-case-views"
import type { UseCasesResult } from "@/hooks/use-cases"
import {
  type CaseViewFilters,
  columnsEqual,
  filtersToViewQuery,
  sortToView,
  viewQueriesEqual,
  viewToFilters,
} from "@/lib/case-views"

const VIEW_PARAM = "view"

/** The view named in the URL, loaded and split for the filter bar. */
export interface ActiveCaseView {
  /** The `?view=` value, or null when no view is open. */
  viewId: string | null
  view: CaseViewRead | null
  mapped: CaseViewFilters | null
  isLoading: boolean
  /** The view could not be loaded, for example because it was deleted. */
  isError: boolean
  /** The open view's filters are in the filter bar, so results can load. */
  isApplied: boolean
  appliedKey: string | null
  setAppliedKey: (key: string | null) => void
  /** Columns chosen for the open view, or null to use the user's own. */
  columns: string[] | null
  setColumns: (columns: string[] | null) => void
  selectView: (viewId: string | null) => void
}

function appliedKeyFor(view: CaseViewRead): string {
  return `${view.id}:${view.updated_at ?? ""}`
}

/**
 * Read `?view=` from the URL and load that case view.
 *
 * Call this before `useCases`, and pass `persist: viewId === null` and
 * `enabled: isApplied` so a view's results never mix with the user's own
 * filters.
 */
export function useActiveCaseView(workspaceId: string): ActiveCaseView {
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const viewId = searchParams?.get(VIEW_PARAM) ?? null
  const { data, isLoading, isError } = useCaseView(workspaceId, viewId)
  const view = viewId !== null && data?.id === viewId ? data : null
  const mapped = useMemo(() => (view ? viewToFilters(view) : null), [view])
  const [appliedKey, setAppliedKey] = useState<string | null>(null)
  const [columns, setColumns] = useState<string[] | null>(null)

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

  return {
    viewId,
    view,
    mapped,
    isLoading,
    isError,
    isApplied:
      viewId === null ||
      (appliedKey !== null && appliedKey.startsWith(`${viewId}:`)),
    appliedKey,
    setAppliedKey,
    columns,
    setColumns,
    selectView,
  }
}

/** What the cases page needs to show and change the open view. */
export interface CaseViewControls {
  active: ActiveCaseView
  /** The open view's filters, sort, or columns differ from its saved ones. */
  isModified: boolean
  visibleColumnIds: string[]
  toggleColumn: (columnId: string) => void
  save: () => Promise<void>
  saveAsNew: (name: string, visibility: CaseViewVisibility) => Promise<void>
  reset: () => void
  isSaving: boolean
}

/**
 * Apply the open view to the filter bar and track changes against it.
 *
 * @param personalColumns - The user's own visible columns, used without a view.
 * @param knownColumnIds - Valid column IDs, for dropping stale saved ones.
 */
export function useCaseViewControls({
  workspaceId,
  active,
  cases,
  personalColumns,
  togglePersonalColumn,
  knownColumnIds,
}: {
  workspaceId: string
  active: ActiveCaseView
  cases: Pick<UseCasesResult, "filters" | "replaceFilters">
  personalColumns: string[]
  togglePersonalColumn: (columnId: string) => void
  knownColumnIds?: Set<string>
}): CaseViewControls {
  const { createView, updateView } = useCaseViewMutations(workspaceId)
  const { view, mapped, appliedKey, setAppliedKey, columns, setColumns } =
    active
  const { replaceFilters } = cases

  useEffect(() => {
    if (!view || !mapped) {
      return
    }
    const key = appliedKeyFor(view)
    if (appliedKey === key) {
      return
    }
    replaceFilters(mapped.filters)
    setColumns(view.columns ?? null)
    setAppliedKey(key)
  }, [view, mapped, appliedKey, replaceFilters, setAppliedKey, setColumns])

  useEffect(() => {
    if (active.viewId === null && appliedKey !== null) {
      setAppliedKey(null)
      setColumns(null)
    }
  }, [active.viewId, appliedKey, setAppliedKey, setColumns])

  const currentQuery = useMemo(
    () => ({
      ...(mapped?.extraQuery ?? {}),
      ...filtersToViewQuery(cases.filters),
    }),
    [cases.filters, mapped]
  )
  const currentSort = sortToView(cases.filters)

  const isModified = useMemo(() => {
    if (!view || !active.isApplied) {
      return false
    }
    const savedOrder = view.order_by ?? DEFAULT_CASE_SORT.field
    const savedSort = view.sort ?? DEFAULT_CASE_SORT.direction
    return (
      !viewQueriesEqual(currentQuery, view.query) ||
      currentSort.order_by !== savedOrder ||
      currentSort.sort !== savedSort ||
      (columns !== null && !columnsEqual(columns, view.columns))
    )
  }, [view, active.isApplied, currentQuery, currentSort, columns])

  const viewColumns =
    view && columns !== null
      ? normalizeVisibleColumnIds(columns, knownColumnIds)
      : null
  const visibleColumnIds = viewColumns ?? personalColumns

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
      const next = base.includes(columnId)
        ? base.filter((id) => id !== columnId)
        : normalizeVisibleColumnIds([...base, columnId], knownColumnIds)
      setColumns(next)
    },
    [
      view,
      columns,
      personalColumns,
      knownColumnIds,
      setColumns,
      togglePersonalColumn,
    ]
  )

  const save = useCallback(async () => {
    if (!view || !view.can_edit) {
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
  }, [view, updateView, currentQuery, currentSort, columns])

  const saveAsNew = useCallback(
    async (name: string, visibility: CaseViewVisibility) => {
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
    ]
  )

  const reset = useCallback(() => {
    if (!view || !mapped) {
      return
    }
    replaceFilters(mapped.filters)
    setColumns(view.columns ?? null)
  }, [view, mapped, replaceFilters, setColumns])

  return {
    active,
    isModified,
    visibleColumnIds,
    toggleColumn,
    save,
    saveAsNew,
    reset,
    isSaving: createView.isPending || updateView.isPending,
  }
}
