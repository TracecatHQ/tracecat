"use client"

import { useEffect } from "react"
import { CasesLayout } from "@/components/cases/cases-layout"
import { useCases } from "@/hooks/use-cases"
import { useEntitlements } from "@/hooks/use-entitlements"
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
    setUpdatedAtSort,
    setLimit,
    goToNextPage,
    goToPreviousPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
  } = useCases()

  const { members } = useWorkspaceMembers(workspaceId)
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")
  const { dropdownDefinitions } = useCaseDropdownDefinitions(
    workspaceId,
    caseAddonsEnabled
  )

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
        onUpdatedAtSortChange={setUpdatedAtSort}
        onLimitChange={setLimit}
        dropdownDefinitions={
          caseAddonsEnabled ? dropdownDefinitions : undefined
        }
        onDropdownFilterChange={setDropdownFilter}
        onDropdownModeChange={setDropdownMode}
        onDropdownSortDirectionChange={setDropdownSortDirection}
        onNextPage={goToNextPage}
        onPreviousPage={goToPreviousPage}
        hasNextPage={hasNextPage}
        hasPreviousPage={hasPreviousPage}
        currentPage={currentPage}
        refetch={refetch}
      />
    </div>
  )
}
