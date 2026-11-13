"use client"

import { useEffect, useMemo } from "react"
import {
  CaseViewChips,
  CaseViewSwitcher,
} from "@/components/cases/case-view-switcher"
import { CasesLayout } from "@/components/cases/cases-layout"
import { useCaseColumnVisibility } from "@/hooks/use-case-column-visibility"
import {
  useActiveCaseView,
  useCaseViewControls,
} from "@/hooks/use-case-view-state"
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
  const { dropdownDefinitions, dropdownDefinitionsIsFetching } =
    useCaseDropdownDefinitions(workspaceId, caseAddonsEnabled)
  const { caseFields, caseFieldsIsFetching } = useCaseFields(workspaceId)
  const { caseDurationDefinitions, caseDurationDefinitionsIsFetching } =
    useCaseDurationDefinitions(workspaceId, caseAddonsEnabled)

  // Avoid pruning persisted selections until entitlement and field metadata
  // have both resolved from the current fetch; stale cached definitions during
  // a refetch could otherwise erase still-valid stored choices.
  const knownColumnIds = useMemo<Set<string> | undefined>(() => {
    if (!hasEntitlementData) {
      return undefined
    }
    if (caseFields === undefined || caseFieldsIsFetching) {
      return undefined
    }
    if (
      caseAddonsEnabled &&
      (dropdownDefinitions === undefined || dropdownDefinitionsIsFetching)
    ) {
      return undefined
    }
    if (
      caseAddonsEnabled &&
      (caseDurationDefinitions === undefined ||
        caseDurationDefinitionsIsFetching)
    ) {
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
    dropdownDefinitionsIsFetching,
    caseFields,
    caseFieldsIsFetching,
    caseDurationDefinitions,
    caseDurationDefinitionsIsFetching,
    caseAddonsEnabled,
  ])

  const {
    visibleColumnIds: personalColumnIds,
    toggleColumn: togglePersonalColumn,
  } = useCaseColumnVisibility(workspaceId, knownColumnIds)
  const activeView = useActiveCaseView(workspaceId)
  const viewColumnIds =
    activeView.view && activeView.columns !== null ? activeView.columns : null
  const visibleColumnIds = viewColumnIds ?? personalColumnIds

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

  const casesResult = useCases({
    fieldIds: selectedFieldIds,
    includeDurations,
    persist: activeView.viewId === null,
    extraQuery: activeView.mapped?.extraQuery,
    enabled: activeView.isApplied,
  })
  const viewControls = useCaseViewControls({
    workspaceId,
    active: activeView,
    cases: casesResult,
    personalColumns: personalColumnIds,
    togglePersonalColumn,
    knownColumnIds,
  })
  const {
    cases,
    isLoading,
    error,
    filters,
    appliedSearchQuery,
    effectiveHierarchy,
    isSearchPending,
    setSearchMode,
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
    setIncludeSubCases,
    totalFilteredCaseEstimate,
    stageCounts,
    isCountsLoading,
    isCountsFetching,
    isCountsError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = casesResult

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
        appliedSearchQuery={appliedSearchQuery}
        effectiveHierarchy={effectiveHierarchy}
        isSearchPending={isSearchPending}
        members={members}
        tags={caseTags}
        onSearchModeChange={setSearchMode}
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
        onIncludeSubCasesChange={
          caseAddonsEnabled ? setIncludeSubCases : undefined
        }
        dropdownDefinitions={
          caseAddonsEnabled ? dropdownDefinitions : undefined
        }
        fieldDefinitions={caseFields}
        durationDefinitions={
          caseAddonsEnabled ? caseDurationDefinitions : undefined
        }
        visibleColumnIds={viewControls.visibleColumnIds}
        onToggleColumn={viewControls.toggleColumn}
        onDropdownFilterChange={setDropdownFilter}
        onDropdownModeChange={setDropdownMode}
        onDropdownSortDirectionChange={setDropdownSortDirection}
        totalFilteredCaseEstimate={totalFilteredCaseEstimate}
        stageCounts={stageCounts}
        isCountsLoading={isCountsLoading}
        isCountsFetching={isCountsFetching}
        isCountsError={isCountsError}
        hasNextPage={hasNextPage}
        isFetchingNextPage={isFetchingNextPage}
        onLoadMore={fetchNextPage}
        viewControls={
          <CaseViewSwitcher workspaceId={workspaceId} controls={viewControls} />
        }
        viewChips={<CaseViewChips controls={viewControls} />}
      />
    </div>
  )
}
