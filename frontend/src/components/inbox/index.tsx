"use client"

import { XIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { type AgentSessionEntity, agentSessionsListSessions } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import type { DateFilterValue, UseInboxFilters } from "@/hooks/use-inbox"
import type { AgentSessionWithStatus } from "@/lib/agents"
import { useWorkspaceId } from "@/providers/workspace-id"
import { ActivityAccordion } from "./activity-accordion"
import { InboxDetail } from "./inbox-detail"
import { InboxEmptyState } from "./inbox-empty-state"
import { InboxHeader } from "./inbox-header"

interface ActivityLayoutProps {
  sessions: AgentSessionWithStatus[]
  selectedId: string | null
  onSelect: (id: string | null) => void
  isLoading: boolean
  error: Error | null
  filters: UseInboxFilters
  onSearchChange: (query: string) => void
  onEntityTypeChange: (type: AgentSessionEntity | "all") => void
  onLimitChange: (limit: number) => void
  onUpdatedAfterChange: (value: DateFilterValue) => void
  onCreatedAfterChange: (value: DateFilterValue) => void
}

export function ActivityLayout({
  sessions,
  selectedId,
  onSelect,
  isLoading,
  error,
  filters,
  onSearchChange,
  onEntityTypeChange,
  onLimitChange,
  onUpdatedAfterChange,
  onCreatedAfterChange,
}: ActivityLayoutProps) {
  const workspaceId = useWorkspaceId()

  // Track the forked session ID and pending message for the currently selected item
  // This is keyed by the selected item ID to prevent cross-contamination
  const [forkedState, setForkedState] = useState<
    Record<string, { sessionId: string; pendingMessage?: string }>
  >({})

  const selectedSession = sessions.find((s) => s.id === selectedId)

  // Fetch existing forked session when selecting a session
  useEffect(() => {
    if (!selectedSession?.id || !workspaceId) return
    // Skip if we already have a forked session for this item
    if (forkedState[selectedSession.id]) return

    const fetchForkedSession = async () => {
      try {
        const childSessions = await agentSessionsListSessions({
          workspaceId,
          parentSessionId: selectedSession.id,
          limit: 1,
        })
        if (childSessions.length > 0) {
          // Use the most recent forked session
          setForkedState((prev) => ({
            ...prev,
            [selectedSession.id]: { sessionId: childSessions[0].id },
          }))
        }
      } catch (err) {
        // Silently fail - user can still fork manually
        console.error("Failed to fetch forked session:", err)
      }
    }

    fetchForkedSession()
  }, [selectedSession?.id, workspaceId, forkedState])

  // Get the forked state for the current item, if any
  const currentForkedState = selectedId ? forkedState[selectedId] : null

  // Use forked session if available, otherwise use the selected session
  const activeSessionId = currentForkedState?.sessionId ?? selectedSession?.id

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

  const handleCloseDetail = () => {
    onSelect(null)
  }

  if (isLoading) {
    return (
      <div className="flex size-full flex-col">
        <InboxHeader
          searchQuery={filters.searchQuery}
          onSearchChange={onSearchChange}
          entityType={filters.entityType}
          onEntityTypeChange={onEntityTypeChange}
          limit={filters.limit}
          onLimitChange={onLimitChange}
          updatedAfter={filters.updatedAfter}
          onUpdatedAfterChange={onUpdatedAfterChange}
          createdAfter={filters.createdAfter}
          onCreatedAfterChange={onCreatedAfterChange}
        />
        <div className="flex flex-1 items-center justify-center">
          <CenteredSpinner />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex size-full flex-col">
        <InboxHeader
          searchQuery={filters.searchQuery}
          onSearchChange={onSearchChange}
          entityType={filters.entityType}
          onEntityTypeChange={onEntityTypeChange}
          limit={filters.limit}
          onLimitChange={onLimitChange}
          updatedAfter={filters.updatedAfter}
          onUpdatedAfterChange={onUpdatedAfterChange}
          createdAfter={filters.createdAfter}
          onCreatedAfterChange={onCreatedAfterChange}
        />
        <div className="flex flex-1 items-center justify-center">
          <span className="text-sm text-red-500">
            Failed to load activity: {error.message}
          </span>
        </div>
      </div>
    )
  }

  if (sessions.length === 0) {
    return (
      <div className="flex size-full flex-col">
        <InboxHeader
          searchQuery={filters.searchQuery}
          onSearchChange={onSearchChange}
          entityType={filters.entityType}
          onEntityTypeChange={onEntityTypeChange}
          limit={filters.limit}
          onLimitChange={onLimitChange}
          updatedAfter={filters.updatedAfter}
          onUpdatedAfterChange={onUpdatedAfterChange}
          createdAfter={filters.createdAfter}
          onCreatedAfterChange={onCreatedAfterChange}
        />
        <div className="flex-1">
          <InboxEmptyState />
        </div>
      </div>
    )
  }

  return (
    <div className="flex size-full flex-col">
      <InboxHeader
        searchQuery={filters.searchQuery}
        onSearchChange={onSearchChange}
        entityType={filters.entityType}
        onEntityTypeChange={onEntityTypeChange}
        limit={filters.limit}
        onLimitChange={onLimitChange}
        updatedAfter={filters.updatedAfter}
        onUpdatedAfterChange={onUpdatedAfterChange}
        createdAfter={filters.createdAfter}
        onCreatedAfterChange={onCreatedAfterChange}
      />
      <div className="flex min-h-0 flex-1">
        {/* Main content: Activity List */}
        <div className="min-w-0 flex-1">
          <ActivityAccordion
            sessions={sessions}
            selectedId={selectedId}
            onSelect={handleSelectItem}
          />
        </div>

        {/* Side panel: Detail view */}
        {selectedId && activeSessionId && selectedSession && (
          <ResizableSidebar initial={450} min={350} max={700}>
            <div className="flex h-full flex-col">
              {/* Header with close button */}
              <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-sm font-medium">
                    {selectedSession.parent_workflow?.alias ||
                      selectedSession.parent_workflow?.title ||
                      selectedSession.title}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {selectedSession.statusLabel}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6 shrink-0"
                  onClick={handleCloseDetail}
                >
                  <XIcon className="size-4" />
                </Button>
              </div>

              {/* Chat content */}
              <div className="min-h-0 flex-1">
                <InboxDetail
                  key={activeSessionId}
                  sessionId={activeSessionId}
                  parentSessionId={selectedSession.id}
                  session={selectedSession}
                  onForked={handleForked}
                  pendingMessage={currentForkedState?.pendingMessage}
                  onPendingMessageSent={handlePendingMessageSent}
                />
              </div>
            </div>
          </ResizableSidebar>
        )}
      </div>
    </div>
  )
}

// Re-export for backward compatibility
export { InboxEmptyState } from "./inbox-empty-state"
