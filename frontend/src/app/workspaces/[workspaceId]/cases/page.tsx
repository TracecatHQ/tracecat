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
  const { hasEntitlement, hasEntitlementData } = useEntitlements()
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

  // Avoid pruning persisted selections until entitlement and field metadata
  // have both resolved; a partial Set during loading would erase stored choices.
  const knownColumnIds = useMemo<Set<string> | undefined>(() => {
    if (!hasEntitlementData) {
      return undefined
    }
    if (caseFields === undefined) {
      return undefined
    }
    if (caseAddonsEnabled && dropdownDefinitions === undefined) {
      return undefined
    }
    if (caseAddonsEnabled && caseDurationDefinitions === undefined) {
      return undefined
    }

    const ids = new Set<string>()
    if (caseAddonsEnabled && dropdownDefinitions) {
      for (const d of dropdownDefinitions) ids.add(`dropdown:${d.ref}`)
    }
    if (caseFields) {
      for (const f of caseFields) {
        if (!f.reserved) ids.add(`field:${f.id}`)
      }
    }
    if (caseAddonsEnabled && caseDurationDefinitions) {
      for (const d of caseDurationDefinitions) ids.add(`duration:${d.id}`)
    }
    return ids
  }, [
    hasEntitlementData,
    dropdownDefinitions,
    caseFields,
    caseDurationDefinitions,
    caseAddonsEnabled,
  ])

  const { visibleColumnIds, toggleColumn } = useCaseColumnVisibility(
    workspaceId,
    knownColumnIds
  )

  const selectedFieldIds = useMemo(() => {
    const validFieldIds = new Set(
      (caseFields ?? [])
        .filter((field) => !field.reserved)
        .map((field) => field.id)
    )
    return visibleColumnIds
      .filter((columnId) => columnId.startsWith("field:"))
      .map((columnId) => columnId.slice("field:".length))
      .filter((fieldId) => validFieldIds.has(fieldId))
  }, [caseFields, visibleColumnIds])

  const includeDurations = useMemo(() => {
    if (!caseAddonsEnabled || !caseDurationDefinitions) {
      return false
    }
    const durationColumnIds = new Set(
      caseDurationDefinitions.map((definition) => `duration:${definition.id}`)
    )
    return visibleColumnIds.some((columnId) => durationColumnIds.has(columnId))
  }, [caseAddonsEnabled, caseDurationDefinitions, visibleColumnIds])

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
  } = useCases({ fieldIds: selectedFieldIds, includeDurations })

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
