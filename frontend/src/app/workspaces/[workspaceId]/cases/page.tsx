"use client"

import { useSearchParams } from "next/navigation"
import { useEffect, useMemo } from "react"
import type {
  CaseDropdownDefinitionRead,
  CaseDurationDefinitionRead,
  CaseFieldReadMinimal,
  CaseTagRead,
  WorkspaceMember,
} from "@/client"
import { CaseViewHeaderPortal } from "@/components/cases/case-view-header-slot"
import {
  CaseViewActions,
  CaseViewChips,
  CaseViewEditorPanel,
  CaseViewMenu,
  useCaseViewEditor,
} from "@/components/cases/case-view-switcher"
import type { CasesViewBar } from "@/components/cases/cases-header"
import { CasesLayout } from "@/components/cases/cases-layout"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { useCaseColumnVisibility } from "@/hooks/use-case-column-visibility"
import {
  type ActiveCaseView,
  useActiveCaseView,
  useCaseViewControls,
  useViewColumns,
  VIEW_PARAM,
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
  const {
    hasEntitlement,
    hasEntitlementData,
    isLoading: entitlementsLoading,
  } = useEntitlements()
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
  // Case views are an enterprise feature (case add-ons).
  const viewsEnabled = caseAddonsEnabled
  const activeView = useActiveCaseView(workspaceId, viewsEnabled)
  const searchParams = useSearchParams()
  const hasViewParam = Boolean(searchParams?.get(VIEW_PARAM))

  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Cases"
    }
  }, [])

  // A ?view= link waits for its view before mounting the list, so the list
  // starts from the view's filters and never shows another search first.
  // Until entitlements load, it is not yet known whether the link applies.
  // If they fail to load, views count as unavailable and the page falls back
  // to the user's own list rather than waiting forever.
  if (hasViewParam && entitlementsLoading) {
    return <CenteredSpinner />
  }
  if (activeView.viewId !== null && !activeView.mapped) {
    return (
      <div className="flex size-full items-center justify-center">
        {activeView.isError ? (
          <div className="flex flex-col items-center gap-2 text-sm text-muted-foreground">
            <span>View not found</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => activeView.selectView(null)}
            >
              Back to cases
            </Button>
          </div>
        ) : (
          <CenteredSpinner />
        )}
      </div>
    )
  }

  return (
    <CasesPageBody
      // One list per view: switching views starts fresh from that view's
      // filters, and closing a view starts again from the user's own.
      key={activeView.viewId ?? "personal"}
      workspaceId={workspaceId}
      viewsEnabled={viewsEnabled}
      activeView={activeView}
      caseAddonsEnabled={caseAddonsEnabled}
      members={members}
      caseTags={caseTags}
      dropdownDefinitions={dropdownDefinitions}
      caseFields={caseFields}
      caseDurationDefinitions={caseDurationDefinitions}
      knownColumnIds={knownColumnIds}
      personalColumnIds={personalColumnIds}
      togglePersonalColumn={togglePersonalColumn}
    />
  )
}

interface CasesPageBodyProps {
  workspaceId: string
  /** Whether case views are available (the case add-ons entitlement). */
  viewsEnabled: boolean
  activeView: ActiveCaseView
  caseAddonsEnabled: boolean
  members: WorkspaceMember[] | undefined
  caseTags: CaseTagRead[] | undefined
  dropdownDefinitions: CaseDropdownDefinitionRead[] | undefined
  caseFields: CaseFieldReadMinimal[] | undefined
  caseDurationDefinitions: CaseDurationDefinitionRead[] | undefined
  knownColumnIds: Set<string> | undefined
  personalColumnIds: string[]
  togglePersonalColumn: (columnId: string) => void
}

/** The case list for one view, or for the user's own filters. */
function CasesPageBody({
  workspaceId,
  viewsEnabled,
  activeView,
  caseAddonsEnabled,
  members,
  caseTags,
  dropdownDefinitions,
  caseFields,
  caseDurationDefinitions,
  knownColumnIds,
  personalColumnIds,
  togglePersonalColumn,
}: CasesPageBodyProps) {
  const viewColumns = useViewColumns({
    view: activeView.view,
    personalColumns: personalColumnIds,
    togglePersonalColumn,
    knownColumnIds,
  })
  const { visibleColumnIds } = viewColumns

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
    view: activeView.mapped ?? undefined,
  })
  const viewControls = useCaseViewControls({
    workspaceId,
    active: activeView,
    cases: casesResult,
    viewColumns,
    personalColumns: personalColumnIds,
  })
  const viewEditor = useCaseViewEditor()
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
    isCountCapped,
    stageCounts,
    isCountsLoading,
    isCountsFetching,
    isCountsError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = casesResult

  const viewBar: CasesViewBar = {
    panel: viewEditor.mode && (
      <CaseViewEditorPanel
        key={viewEditor.mode.kind}
        workspaceId={workspaceId}
        controls={viewControls}
        editor={viewEditor}
        mode={viewEditor.mode}
      />
    ),
    chips: <CaseViewChips controls={viewControls} />,
    renderActions: ({ hasFilters, clearFilters }) => (
      <CaseViewActions
        controls={viewControls}
        editor={viewEditor}
        hasFilters={hasFilters}
        clearFilters={clearFilters}
      />
    ),
  }

  return (
    <div className="size-full overflow-hidden">
      {viewsEnabled && (
        <CaseViewHeaderPortal>
          <CaseViewMenu
            workspaceId={workspaceId}
            controls={viewControls}
            editor={viewEditor}
          />
        </CaseViewHeaderPortal>
      )}
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
        onIncludeSubCasesChange={setIncludeSubCases}
        dropdownDefinitions={
          caseAddonsEnabled ? dropdownDefinitions : undefined
        }
        fieldDefinitions={caseFields}
        durationDefinitions={
          caseAddonsEnabled ? caseDurationDefinitions : undefined
        }
        visibleColumnIds={visibleColumnIds}
        onToggleColumn={viewColumns.toggleColumn}
        onDropdownFilterChange={setDropdownFilter}
        onDropdownModeChange={setDropdownMode}
        onDropdownSortDirectionChange={setDropdownSortDirection}
        totalFilteredCaseEstimate={totalFilteredCaseEstimate}
        isCountCapped={isCountCapped}
        stageCounts={stageCounts}
        isCountsLoading={isCountsLoading}
        isCountsFetching={isCountsFetching}
        isCountsError={isCountsError}
        hasNextPage={hasNextPage}
        isFetchingNextPage={isFetchingNextPage}
        onLoadMore={fetchNextPage}
        viewBar={viewsEnabled ? viewBar : undefined}
      />
    </div>
  )
}
