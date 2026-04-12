"use client"

import { useCallback, useEffect, useState } from "react"

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
    return parsed
      .filter((item): item is string => typeof item === "string")
      .slice(0, MAX_VISIBLE_COLUMNS)
  } catch {
    return []
  }
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

export function useCaseColumnVisibility(
  workspaceId: string
): UseCaseColumnVisibilityResult {
  const [visibleColumnIds, setVisibleColumnIds] = useState<string[]>(() =>
    loadPersistedColumns(workspaceId)
  )

  useEffect(() => {
    persistColumns(workspaceId, visibleColumnIds)
  }, [workspaceId, visibleColumnIds])

  const toggleColumn = useCallback((columnId: string) => {
    setVisibleColumnIds((prev) => {
      if (prev.includes(columnId)) {
        return prev.filter((id) => id !== columnId)
      }
      if (prev.length >= MAX_VISIBLE_COLUMNS) {
        return prev
      }
      return [...prev, columnId]
    })
  }, [])

  return {
    visibleColumnIds,
    toggleColumn,
  }
}
