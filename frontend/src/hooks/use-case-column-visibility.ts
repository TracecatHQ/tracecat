"use client"

import { useCallback, useEffect, useRef, useState } from "react"

const STORAGE_KEY = "cases-visible-columns:v1"
const MAX_VISIBLE_COLUMNS = 4

function getStorageKey(workspaceId: string): string {
  return `${workspaceId}:${STORAGE_KEY}`
}

function loadPersistedColumns(workspaceId: string): string[] {
  if (typeof window === "undefined") {
    return []
  }
  try {
    const stored = window.localStorage.getItem(getStorageKey(workspaceId))
    if (!stored) {
      return []
    }
    const parsed: unknown = JSON.parse(stored)
    if (!Array.isArray(parsed)) {
      return []
    }
    return parsed.filter((item): item is string => typeof item === "string")
  } catch {
    return []
  }
}

function normalizeVisibleColumnIds(
  columnIds: string[],
  knownColumnIds?: Set<string>
): string[] {
  const normalized: string[] = []
  const seen = new Set<string>()

  for (const columnId of columnIds) {
    if (seen.has(columnId)) {
      continue
    }
    if (knownColumnIds && !knownColumnIds.has(columnId)) {
      continue
    }
    normalized.push(columnId)
    seen.add(columnId)
    if (normalized.length >= MAX_VISIBLE_COLUMNS) {
      break
    }
  }

  return normalized
}

function persistColumns(workspaceId: string, columns: string[]): void {
  if (typeof window === "undefined") {
    return
  }
  try {
    window.localStorage.setItem(
      getStorageKey(workspaceId),
      JSON.stringify(columns)
    )
  } catch {
    // Ignore storage errors
  }
}

export interface UseCaseColumnVisibilityResult {
  visibleColumnIds: string[]
  toggleColumn: (columnId: string) => void
}

interface VisibleColumnsState {
  workspaceId: string
  visibleColumnIds: string[]
}

/**
 * @param knownColumnIds - Set of currently valid column IDs from loaded
 *   definitions. When provided, stale persisted IDs are pruned and the final
 *   stored selection is normalized to at most four unique visible columns.
 */
export function useCaseColumnVisibility(
  workspaceId: string,
  knownColumnIds?: Set<string>
): UseCaseColumnVisibilityResult {
  const loadColumnsForWorkspace = useCallback(
    (targetWorkspaceId: string) =>
      normalizeVisibleColumnIds(
        loadPersistedColumns(targetWorkspaceId),
        knownColumnIds
      ),
    [knownColumnIds]
  )
  const [state, setState] = useState<VisibleColumnsState>(() => ({
    workspaceId,
    visibleColumnIds: loadColumnsForWorkspace(workspaceId),
  }))

  // Keep the rendered selection scoped to the active workspace so a route-level
  // workspace switch never exposes or persists the previous workspace's columns.
  const visibleColumnIds =
    state.workspaceId === workspaceId
      ? state.visibleColumnIds
      : loadColumnsForWorkspace(workspaceId)

  useEffect(() => {
    if (state.workspaceId !== workspaceId) {
      setState({
        workspaceId,
        visibleColumnIds: loadColumnsForWorkspace(workspaceId),
      })
    }
  }, [loadColumnsForWorkspace, state.workspaceId, workspaceId])

  useEffect(() => {
    setState((prev) => {
      if (prev.workspaceId !== workspaceId) {
        return prev
      }
      const normalized = normalizeVisibleColumnIds(
        prev.visibleColumnIds,
        knownColumnIds
      )
      return prev.visibleColumnIds.length === normalized.length &&
        prev.visibleColumnIds.every(
          (columnId, index) => columnId === normalized[index]
        )
        ? prev
        : { workspaceId, visibleColumnIds: normalized }
    })
  }, [knownColumnIds, workspaceId])

  useEffect(() => {
    if (state.workspaceId !== workspaceId) {
      return
    }
    persistColumns(workspaceId, state.visibleColumnIds)
  }, [state, workspaceId])

  // Ref so toggleColumn always sees the latest set without re-creating
  const knownRef = useRef(knownColumnIds)
  knownRef.current = knownColumnIds

  const toggleColumn = useCallback(
    (columnId: string) => {
      setState((prev) => {
        const baseColumns =
          prev.workspaceId === workspaceId
            ? prev.visibleColumnIds
            : loadColumnsForWorkspace(workspaceId)
        const normalizedPrev = normalizeVisibleColumnIds(
          baseColumns,
          knownRef.current
        )
        if (normalizedPrev.includes(columnId)) {
          return {
            workspaceId,
            visibleColumnIds: normalizedPrev.filter((id) => id !== columnId),
          }
        }
        return {
          workspaceId,
          visibleColumnIds: normalizeVisibleColumnIds(
            [...normalizedPrev, columnId],
            knownRef.current
          ),
        }
      })
    },
    [loadColumnsForWorkspace, workspaceId]
  )

  return {
    visibleColumnIds,
    toggleColumn,
  }
}
