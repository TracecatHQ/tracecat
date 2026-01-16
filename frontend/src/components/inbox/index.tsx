"use client"

import { useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import type { InboxItem } from "@/lib/inbox"
import { InboxDetail } from "./inbox-detail"
import { InboxEmptyState } from "./inbox-empty-state"
import { InboxList } from "./inbox-list"

interface InboxLayoutProps {
  items: InboxItem[]
  selectedId: string | null
  onSelect: (id: string) => void
  isLoading: boolean
  error: Error | null
}

export function InboxLayout({
  items,
  selectedId,
  onSelect,
  isLoading,
  error,
}: InboxLayoutProps) {
  // Track the forked session ID and pending message for the currently selected item
  // This is keyed by the selected item ID to prevent cross-contamination
  const [forkedState, setForkedState] = useState<
    Record<string, { sessionId: string; pendingMessage?: string }>
  >({})

  const selectedItem = items.find((item) => item.id === selectedId)

  // Get the forked state for the current item, if any
  const currentForkedState = selectedId ? forkedState[selectedId] : null

  // Use forked session if available, otherwise use the selected item's source
  const activeSessionId =
    currentForkedState?.sessionId ?? selectedItem?.source_id

  const handleForked = (forkedId: string, pendingMessage: string) => {
    if (!selectedId) return
    setForkedState((prev) => ({
      ...prev,
      [selectedId]: { sessionId: forkedId, pendingMessage },
    }))
  }

  const handlePendingMessageSent = () => {
    if (!selectedId) return
    setForkedState((prev) => {
      const current = prev[selectedId]
      if (!current) return prev
      return {
        ...prev,
        [selectedId]: { ...current, pendingMessage: undefined },
      }
    })
  }

  const handleSelectItem = (id: string) => {
    onSelect(id)
  }

  if (isLoading) {
    return (
      <div className="flex size-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex size-full items-center justify-center">
        <span className="text-sm text-red-500">
          Failed to load inbox: {error.message}
        </span>
      </div>
    )
  }

  if (items.length === 0) {
    return <InboxEmptyState />
  }

  return (
    <div className="flex size-full">
      {/* Left panel: List */}
      <div className="w-80 shrink-0 border-r">
        <InboxList
          items={items}
          selectedId={selectedId}
          onSelect={handleSelectItem}
        />
      </div>

      {/* Right panel: Detail */}
      <div className="min-w-0 flex-1">
        {activeSessionId && selectedItem ? (
          <InboxDetail
            // Key by activeSessionId to force remount when switching to forked session
            key={activeSessionId}
            sessionId={activeSessionId}
            parentSessionId={selectedItem.source_id}
            item={selectedItem}
            onForked={handleForked}
            pendingMessage={currentForkedState?.pendingMessage}
            onPendingMessageSent={handlePendingMessageSent}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <span className="text-sm text-muted-foreground">
              Select an item to view details
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
