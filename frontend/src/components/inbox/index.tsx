"use client"

import { useEffect } from "react"
import { useInboxChat } from "@/app/workspaces/[workspaceId]/inbox/layout"
import type { AgentSessionEntity } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import type { DateFilterValue, UseInboxFilters } from "@/hooks/use-inbox"
import type { AgentSessionWithStatus } from "@/lib/agents"
import { ActivityAccordion } from "./activity-accordion"
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
  const { setSelectedSession, setChatOpen, registerOnClose } = useInboxChat()

  // Sync selected session with layout context
  const selectedSession = sessions.find((s) => s.id === selectedId) ?? null

  // Register callback to clear selection when chat is closed from layout
  useEffect(() => {
    registerOnClose(() => onSelect(null))
  }, [registerOnClose, onSelect])

  useEffect(() => {
    setSelectedSession(selectedSession)
    setChatOpen(!!selectedSession)
  }, [selectedSession, setSelectedSession, setChatOpen])

  const handleSelectItem = (id: string) => {
    onSelect(id)
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
      <div className="min-h-0 flex-1">
        <ActivityAccordion
          sessions={sessions}
          selectedId={selectedId}
          onSelect={handleSelectItem}
        />
      </div>
    </div>
  )
}
