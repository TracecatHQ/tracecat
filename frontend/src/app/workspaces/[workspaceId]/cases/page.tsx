"use client"

import { useEffect } from "react"
import { CasesLayout } from "@/components/cases/cases-layout"
import { useCases } from "@/hooks/use-cases"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { useCaseDropdownDefinitions, useCaseTagCatalog } from "@/lib/hooks"
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
    setPrioritySortDirection,
    setSeverityFilter,
    setSeverityMode,
    setSeveritySortDirection,
    setAssigneeFilter,
    setAssigneeMode,
    setAssigneeSortDirection,
    setTagFilter,
    setTagMode,
    setTagSortDirection,
    setDropdownFilter,
    setDropdownMode,
    setDropdownSortDirection,
    setUpdatedAfter,
    setCreatedAfter,
  } = useCases()

  const { members } = useWorkspaceMembers(workspaceId)
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { isFeatureEnabled } = useFeatureFlag()
  const caseDropdownsEnabled = isFeatureEnabled("case-dropdowns")
  const { dropdownDefinitions } = useCaseDropdownDefinitions(workspaceId)

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
        onPrioritySortDirectionChange={setPrioritySortDirection}
        onSeverityChange={setSeverityFilter}
        onSeverityModeChange={setSeverityMode}
        onSeveritySortDirectionChange={setSeveritySortDirection}
        onAssigneeChange={setAssigneeFilter}
        onAssigneeModeChange={setAssigneeMode}
        onAssigneeSortDirectionChange={setAssigneeSortDirection}
        onTagChange={setTagFilter}
        onTagModeChange={setTagMode}
        onTagSortDirectionChange={setTagSortDirection}
        onUpdatedAfterChange={setUpdatedAfter}
        onCreatedAfterChange={setCreatedAfter}
        dropdownDefinitions={
          caseDropdownsEnabled ? dropdownDefinitions : undefined
        }
        onDropdownFilterChange={setDropdownFilter}
        onDropdownModeChange={setDropdownMode}
        onDropdownSortDirectionChange={setDropdownSortDirection}
        refetch={refetch}
      />
    </div>
  )
}
