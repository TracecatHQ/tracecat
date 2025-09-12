"use client"

import { useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import type { SortOption } from "@/components/runbooks/runbooks-grid-view"
import { RunbooksGridView } from "@/components/runbooks/runbooks-grid-view"
import { useListPrompts } from "@/hooks/use-prompt"
import { useWorkspaceId } from "@/providers/workspace-id"

export function RunbooksDashboard() {
  const workspaceId = useWorkspaceId()
  const [sortBy, setSortBy] = useState<SortOption>("updated_at")

  const {
    data: prompts,
    isLoading,
    error,
  } = useListPrompts({
    workspaceId,
    limit: 100,
    sortBy,
    order: "desc",
  })

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <div className="text-center text-red-600">
          Error loading runbooks: {error.message}
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container mx-auto h-full p-6">
        <RunbooksGridView
          runbooks={prompts || []}
          isLoading={isLoading}
          sortBy={sortBy}
          onSortChange={setSortBy}
        />
      </div>
    </div>
  )
}
