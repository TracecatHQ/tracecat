"use client"

import { useEffect, useMemo } from "react"
import { CasesLayout } from "@/components/cases/cases-layout"
import { useCaseColumnVisibility } from "@/hooks/use-case-column-visibility"
import { useCases } from "@/hooks/use-cases"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import {
  useCaseDropdownDefinitions,
  useCaseDurationDefinitions,
  useCaseFields,
  useCaseTagCatalog,
} from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function CasesPage() {
  const workspaceId = useWorkspaceId()
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")

  const { members } = useWorkspaceMembers(workspaceId)
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { dropdownDefinitions } = useCaseDropdownDefinitions(
    workspaceId,
    caseAddonsEnabled
  )
  const { caseFields } = useCaseFields(workspaceId)
  const { caseDurationDefinitions } = useCaseDurationDefinitions(
    workspaceId,
    caseAddonsEnabled
  )

  // Build the set of valid column IDs from loaded definitions so the hook
  // can prune stale persisted selections (e.g. deleted definitions).
  // undefined while any definition list is still loading → skip pruning.
  const validColumnIds = useMemo(() => {
    const dropdowns = caseAddonsEnabled ? dropdownDefinitions : undefined
    const durations = caseAddonsEnabled ? caseDurationDefinitions : undefined
    // Fields are not gated by case_addons
    if (!caseFields || (caseAddonsEnabled && (!dropdowns || !durations))) {
      return undefined
    }
    const ids = new Set<string>()
    if (dropdowns) {
      for (const d of dropdowns) ids.add(`dropdown:${d.ref}`)
    }
    for (const f of caseFields) {
      if (!f.reserved) ids.add(`field:${f.id}`)
    }
    if (durations) {
      for (const d of durations) ids.add(`duration:${d.id}`)
    }
    return ids
  }, [
    dropdownDefinitions,
    caseFields,
    caseDurationDefinitions,
    caseAddonsEnabled,
  ])

  const { visibleColumnIds, toggleColumn } = useCaseColumnVisibility(
    workspaceId,
    validColumnIds
  )

  const includeFields = visibleColumnIds.some((id) => id.startsWith("field:"))

  const {
    cases,
    isLoading,
    error,
    filters,
    refetch,
    setSearchQuery,
    setSortBy,
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
    totalFilteredCaseEstimate,
    stageCounts,
    isCountsLoading,
    isCountsFetching,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useCases({ includeFields })

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
        onSortByChange={setSortBy}
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
          caseAddonsEnabled ? dropdownDefinitions : undefined
        }
        fieldDefinitions={caseFields}
        durationDefinitions={
          caseAddonsEnabled ? caseDurationDefinitions : undefined
        }
        visibleColumnIds={visibleColumnIds}
        onToggleColumn={toggleColumn}
        onDropdownFilterChange={setDropdownFilter}
        onDropdownModeChange={setDropdownMode}
        onDropdownSortDirectionChange={setDropdownSortDirection}
        totalFilteredCaseEstimate={totalFilteredCaseEstimate}
        stageCounts={stageCounts}
        isCountsLoading={isCountsLoading}
        isCountsFetching={isCountsFetching}
        hasNextPage={hasNextPage}
        isFetchingNextPage={isFetchingNextPage}
        onLoadMore={fetchNextPage}
        refetch={refetch}
      />
    </div>
  )
}
