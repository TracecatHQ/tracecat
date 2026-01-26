"use client"

import { useEffect } from "react"
import { CasesLayout } from "@/components/cases/cases-layout"
import { useCases } from "@/hooks/use-cases"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function CasesPage() {
  const workspaceId = useWorkspaceId()

  const {
    cases,
    isLoading,
    error,
    filters,
    refetch,
    setSearchQuery,
    setStatusFilter,
    setStatusMode,
    setPriorityFilter,
    setPriorityMode,
    setSeverityFilter,
    setSeverityMode,
    setAssigneeFilter,
    setAssigneeMode,
    setTagFilter,
    setTagMode,
    setUpdatedAfter,
    setCreatedAfter,
  } = useCases()

  const { members } = useWorkspaceMembers(workspaceId)
  const { caseTags } = useCaseTagCatalog(workspaceId)

  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Cases"
    }
  }, [])

  return (
    <div className="size-full overflow-hidden">
      <CasesLayout
        cases={cases}
        isLoading={isLoading}
        error={error}
        filters={filters}
        members={members}
        tags={caseTags}
        onSearchChange={setSearchQuery}
        onStatusChange={setStatusFilter}
        onStatusModeChange={setStatusMode}
        onPriorityChange={setPriorityFilter}
        onPriorityModeChange={setPriorityMode}
        onSeverityChange={setSeverityFilter}
        onSeverityModeChange={setSeverityMode}
        onAssigneeChange={setAssigneeFilter}
        onAssigneeModeChange={setAssigneeMode}
        onTagChange={setTagFilter}
        onTagModeChange={setTagMode}
        onUpdatedAfterChange={setUpdatedAfter}
        onCreatedAfterChange={setCreatedAfter}
        refetch={refetch}
      />
    </div>
  )
}
