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

/**
 * @param knownColumnIds - Set of currently valid column IDs from loaded
 *   definitions. When provided, stale persisted IDs are pruned and the final
 *   stored selection is normalized to at most four unique visible columns.
 */
export function useCaseColumnVisibility(
  workspaceId: string,
  knownColumnIds?: Set<string>
): UseCaseColumnVisibilityResult {
  const [visibleColumnIds, setVisibleColumnIds] = useState<string[]>(() =>
    normalizeVisibleColumnIds(loadPersistedColumns(workspaceId), knownColumnIds)
  )

  // Reload from localStorage when workspace changes
  const prevWorkspaceId = useRef(workspaceId)
  useEffect(() => {
    if (prevWorkspaceId.current !== workspaceId) {
      prevWorkspaceId.current = workspaceId
      setVisibleColumnIds(
        normalizeVisibleColumnIds(
          loadPersistedColumns(workspaceId),
          knownColumnIds
        )
      )
    }
  }, [knownColumnIds, workspaceId])

  useEffect(() => {
    setVisibleColumnIds((prev) => {
      const normalized = normalizeVisibleColumnIds(prev, knownColumnIds)
      return prev.length === normalized.length &&
        prev.every((columnId, index) => columnId === normalized[index])
        ? prev
        : normalized
    })
  }, [knownColumnIds])

  useEffect(() => {
    persistColumns(workspaceId, visibleColumnIds)
  }, [workspaceId, visibleColumnIds])

  // Ref so toggleColumn always sees the latest set without re-creating
  const knownRef = useRef(knownColumnIds)
  knownRef.current = knownColumnIds

  const toggleColumn = useCallback((columnId: string) => {
    setVisibleColumnIds((prev) => {
      const normalizedPrev = normalizeVisibleColumnIds(prev, knownRef.current)
      if (normalizedPrev.includes(columnId)) {
        return normalizedPrev.filter((id) => id !== columnId)
      }
      return normalizeVisibleColumnIds(
        [...normalizedPrev, columnId],
        knownRef.current
      )
    })
  }, [])

  return {
    visibleColumnIds,
    toggleColumn,
  }
}
