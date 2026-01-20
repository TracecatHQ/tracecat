"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { type InboxItemRead, inboxListItems } from "@/client"
import { useWorkspaceId } from "@/providers/workspace-id"

export interface UseInboxOptions {
  enabled?: boolean
}

export interface UseInboxResult {
  items: InboxItemRead[]
  selectedId: string | null
  setSelectedId: (id: string | null) => void
  isLoading: boolean
  error: Error | null
}

export function useInbox(options: UseInboxOptions = {}): UseInboxResult {
  const { enabled = true } = options
  const workspaceId = useWorkspaceId()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Fetch inbox items from unified endpoint
  const {
    data: inboxData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["inbox", "list", workspaceId],
    queryFn: () => inboxListItems({ workspaceId }),
    refetchInterval: (query) => {
      // Poll faster if there are pending items
      const hasPending = query.state.data?.some(
        (item) => item.status === "pending"
      )
      return hasPending ? 3000 : 10000
    },
    enabled,
  })

  // Backend returns items already sorted by status priority (pending first) then by created_at desc
  const items = inboxData ?? []

  // Auto-select first pending item or first item, and clear stale selections
  useEffect(() => {
    if (items.length === 0) {
      // Clear selection when list becomes empty
      if (selectedId !== null) {
        setSelectedId(null)
      }
      return
    }

    // Check if current selection still exists in the list
    const selectionExists =
      selectedId !== null && items.some((item) => item.id === selectedId)

    if (!selectionExists) {
      // Re-select: prefer first pending item, otherwise first item
      const pendingItem = items.find((item) => item.status === "pending")
      setSelectedId(pendingItem?.id ?? items[0].id)
    }
  }, [items, selectedId])

  return {
    items,
    selectedId,
    setSelectedId,
    isLoading,
    error: error ?? null,
  }
}
