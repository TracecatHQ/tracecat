"use client"

import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  CaseBatchResponse,
  CaseDropdownDefinitionRead,
  CaseDurationDefinitionRead,
  CaseFieldReadMinimal,
  CasePriority,
  CaseReadMinimal,
  CaseSearchAggregateRead,
  CaseSeverity,
  CaseStatus,
  CaseTagRead,
  CaseUpdate,
  WorkspaceMember,
} from "@/client"
import { useCaseSelection } from "@/components/cases/case-selection-context"
import {
  type CaseSortValue,
  DEFAULT_CASE_SORT,
} from "@/components/cases/case-sort"
import { CasesAccordion } from "@/components/cases/cases-accordion"
import { CasesHeader } from "@/components/cases/cases-header"
import { DeleteCaseAlertDialog } from "@/components/cases/delete-case-dialog"
import type {
  FilterMode,
  SortDirection,
} from "@/components/filters/filter-multi-select"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useToast } from "@/components/ui/use-toast"
import type { CaseDateFilterValue, UseCasesFilters } from "@/hooks/use-cases"
import { useEntitlements } from "@/hooks/use-entitlements"
import { runChunkedCaseBatch } from "@/lib/cases/batch"
import { invalidateCaseHierarchy } from "@/lib/cases/invalidation"
import { caseHref } from "@/lib/cases/urls"
import {
  useBatchChangeCaseParent,
  useBatchDeleteCases,
  useBatchUpdateCases,
} from "@/lib/hooks"
import { useQueryClient } from "@/lib/query"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CasesLayoutProps {
  cases: CaseReadMinimal[]
  isLoading: boolean
  error: Error | null
  filters: UseCasesFilters
  members?: WorkspaceMember[]
  tags?: CaseTagRead[]
  onSearchChange: (query: string) => void
  onSortByChange: (value: CaseSortValue) => void
  onStatusChange: (status: CaseStatus[]) => void
  onStatusModeChange: (mode: FilterMode) => void
  onPriorityChange: (priority: CasePriority[]) => void
  onPriorityModeChange: (mode: FilterMode) => void
  onPrioritySortDirectionChange: (direction: SortDirection) => void
  onSeverityChange: (severity: CaseSeverity[]) => void
  onSeverityModeChange: (mode: FilterMode) => void
  onSeveritySortDirectionChange: (direction: SortDirection) => void
  onAssigneeChange: (assignee: string[]) => void
  onAssigneeModeChange: (mode: FilterMode) => void
  onAssigneeSortDirectionChange: (direction: SortDirection) => void
  onTagChange: (tags: string[]) => void
  onTagModeChange: (mode: FilterMode) => void
  onTagSortDirectionChange: (direction: SortDirection) => void
  onUpdatedAfterChange: (value: CaseDateFilterValue) => void
  onCreatedAfterChange: (value: CaseDateFilterValue) => void
  onIncludeSubCasesChange?: (value: boolean) => void
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  fieldDefinitions?: CaseFieldReadMinimal[]
  durationDefinitions?: CaseDurationDefinitionRead[]
  visibleColumnIds?: string[]
  onToggleColumn?: (columnId: string) => void
  onDropdownFilterChange: (ref: string, values: string[]) => void
  onDropdownModeChange: (ref: string, mode: FilterMode) => void
  onDropdownSortDirectionChange: (ref: string, direction: SortDirection) => void
  totalFilteredCaseEstimate: number | null
  stageCounts: CaseSearchAggregateRead["status_groups"] | null
  isCountsLoading: boolean
  isCountsFetching: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  onLoadMore: () => void
}

export function CasesLayout({
  cases,
  isLoading,
  error,
  filters,
  members,
  tags,
  onSearchChange,
  onSortByChange,
  onStatusChange,
  onStatusModeChange,
  onPriorityChange,
  onPriorityModeChange,
  onPrioritySortDirectionChange,
  onSeverityChange,
  onSeverityModeChange,
  onSeveritySortDirectionChange,
  onAssigneeChange,
  onAssigneeModeChange,
  onAssigneeSortDirectionChange,
  onTagChange,
  onTagModeChange,
  onTagSortDirectionChange,
  onUpdatedAfterChange,
  onCreatedAfterChange,
  onIncludeSubCasesChange,
  dropdownDefinitions,
  fieldDefinitions,
  durationDefinitions,
  visibleColumnIds,
  onToggleColumn,
  onDropdownFilterChange,
  onDropdownModeChange,
  onDropdownSortDirectionChange,
  totalFilteredCaseEstimate,
  stageCounts,
  isCountsLoading,
  isCountsFetching,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
}: CasesLayoutProps) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedCaseIds, setSelectedCaseIds] = useState<Set<string>>(new Set())
  const [isDeleting, setIsDeleting] = useState(false)
  const [isBulkUpdating, setIsBulkUpdating] = useState(false)
  const [isChangingParent, setIsChangingParent] = useState(false)
  const [caseToDelete, setCaseToDelete] = useState<CaseReadMinimal | null>(null)

  const { updateSelection, resetSelection } = useCaseSelection()
  const queryClient = useQueryClient()
  const { batchDeleteCases } = useBatchDeleteCases({ workspaceId })
  const { batchUpdateCases } = useBatchUpdateCases({ workspaceId })
  const { batchChangeCaseParent } = useBatchChangeCaseParent({ workspaceId })
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")
  const { toast } = useToast()

  const handleSelectCase = useCallback(
    (id: string) => {
      setSelectedId(id)
      if (workspaceId) {
        router.push(caseHref(workspaceId, id))
      }
    },
    [workspaceId, router]
  )

  const handleCheckChange = useCallback((id: string, checked: boolean) => {
    setSelectedCaseIds((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(id)
      } else {
        next.delete(id)
      }
      return next
    })
  }, [])

  const handleClearSelection = useCallback(() => {
    setSelectedCaseIds(new Set())
  }, [])

  const handleSelectAll = useCallback(() => {
    setSelectedCaseIds(new Set(cases.map((c) => c.id)))
  }, [cases])

  const handleDeselectAll = useCallback(() => {
    setSelectedCaseIds(new Set())
  }, [])

  /**
   * Run a batch action on the selection. On partial failure the succeeded
   * cases leave the selection, so a retry only targets the remainder.
   */
  const runSelectionBatch = useCallback(
    async ({
      request,
      verb,
      failureTitle,
      successTitle,
      successDescription,
      clearOnSuccess,
      setBusy,
      invalidate,
    }: {
      request: (chunk: string[]) => Promise<CaseBatchResponse>
      /** Past tense used in partial-failure toasts, e.g. "deleted". */
      verb: string
      failureTitle: string
      successTitle: string
      successDescription: string
      clearOnSuccess: boolean
      setBusy: (busy: boolean) => void
      invalidate: () => Promise<void>
    }) => {
      if (selectedCaseIds.size === 0) return
      const caseIds = Array.from(selectedCaseIds)
      setBusy(true)
      try {
        const result = await runChunkedCaseBatch(caseIds, request)
        const { succeededIds } = result
        if (result.error || result.failed > 0) {
          if (result.error) {
            console.error(`Failed batch case action (${verb}):`, result.error)
          }
          toast({
            variant: "destructive",
            title: result.error
              ? succeededIds.size > 0
                ? `${succeededIds.size} ${verb} before a request failed`
                : failureTitle
              : `${succeededIds.size} ${verb}, ${result.failed} failed`,
            description: result.error
              ? "Please retry the remaining cases."
              : (result.errors[0] ??
                `Some selected cases could not be ${verb}.`),
          })
          // Keep selection changes made mid-flight; only drop what succeeded.
          setSelectedCaseIds(
            (prev) => new Set([...prev].filter((id) => !succeededIds.has(id)))
          )
        } else {
          toast({ title: successTitle, description: successDescription })
          if (clearOnSuccess) {
            setSelectedCaseIds(new Set())
          }
        }
      } finally {
        // Refetch once per bulk operation, not once per chunk.
        await invalidate()
        setBusy(false)
      }
    },
    [selectedCaseIds, toast]
  )

  const invalidateCases = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["cases"], exact: false }),
    [queryClient]
  )

  const handleBulkDelete = useCallback(
    () =>
      runSelectionBatch({
        request: (chunk) => batchDeleteCases({ case_ids: chunk }),
        verb: "deleted",
        failureTitle: "Failed to delete cases",
        successTitle: `${selectedCaseIds.size} case(s) deleted`,
        successDescription:
          "The selected cases have been deleted successfully.",
        clearOnSuccess: true,
        setBusy: setIsDeleting,
        invalidate: invalidateCases,
      }),
    [batchDeleteCases, invalidateCases, runSelectionBatch, selectedCaseIds]
  )

  const handleBulkUpdate = useCallback(
    (
      updates: Partial<CaseUpdate>,
      options?: { successTitle?: string; successDescription?: string }
    ) => {
      const count = selectedCaseIds.size
      return runSelectionBatch({
        request: (chunk) =>
          batchUpdateCases({ case_ids: chunk, update: updates }),
        verb: "updated",
        failureTitle: "Failed to update cases",
        successTitle:
          options?.successTitle ||
          `Updated ${count} case${count > 1 ? "s" : ""}`,
        successDescription:
          options?.successDescription ||
          "The selected cases have been updated successfully.",
        clearOnSuccess: false,
        setBusy: setIsBulkUpdating,
        invalidate: invalidateCases,
      })
    },
    [batchUpdateCases, invalidateCases, runSelectionBatch, selectedCaseIds]
  )

  const handleSetParent = useCallback(
    (parent: { id: string; short_id: string } | null) => {
      const count = selectedCaseIds.size
      return runSelectionBatch({
        request: (chunk) =>
          batchChangeCaseParent({
            caseIds: chunk,
            parentId: parent?.id ?? null,
          }),
        verb: "updated",
        failureTitle: parent
          ? "Failed to group cases"
          : "Failed to remove from parent",
        successTitle: parent
          ? `Grouped under ${parent.short_id}`
          : "Removed from parent",
        successDescription: `Applied to ${count} case${count === 1 ? "" : "s"}.`,
        clearOnSuccess: true,
        setBusy: setIsChangingParent,
        invalidate: () => invalidateCaseHierarchy(queryClient),
      })
    },
    [batchChangeCaseParent, queryClient, runSelectionBatch, selectedCaseIds]
  )

  // Sync selection state with context
  useEffect(() => {
    if (selectedCaseIds.size > 0) {
      updateSelection({
        selectedCount: selectedCaseIds.size,
        selectedCaseIds: Array.from(selectedCaseIds),
        clearSelection: handleClearSelection,
        deleteSelected: handleBulkDelete,
        bulkUpdateSelectedCases: handleBulkUpdate,
        setParentForSelectedCases: caseAddonsEnabled
          ? handleSetParent
          : undefined,
        isDeleting,
        isUpdating: isBulkUpdating,
        isChangingParent,
      })
    } else {
      resetSelection()
    }
  }, [
    selectedCaseIds,
    handleClearSelection,
    handleBulkDelete,
    handleBulkUpdate,
    handleSetParent,
    caseAddonsEnabled,
    isDeleting,
    isBulkUpdating,
    isChangingParent,
    resetSelection,
    updateSelection,
  ])

  // Reset selection when component unmounts
  useEffect(() => () => resetSelection(), [resetSelection])

  const selectedCaseIdsSet = useMemo(() => selectedCaseIds, [selectedCaseIds])
  const fieldMetadataById = useMemo<
    | ReadonlyMap<string, Pick<CaseFieldReadMinimal, "display_name" | "type">>
    | undefined
  >(() => {
    if (!fieldDefinitions) {
      return undefined
    }

    return new Map(
      fieldDefinitions.map((field) => [
        field.id,
        { display_name: field.display_name, type: field.type },
      ])
    )
  }, [fieldDefinitions])
  const durationNamesById = useMemo<
    ReadonlyMap<CaseDurationDefinitionRead["id"], string> | undefined
  >(() => {
    if (!durationDefinitions) {
      return undefined
    }

    return new Map(
      durationDefinitions.map((duration) => [duration.id, duration.name])
    )
  }, [durationDefinitions])

  const handleDeleteRequest = useCallback((caseData: CaseReadMinimal) => {
    setCaseToDelete(caseData)
  }, [])

  const headerProps = {
    searchQuery: filters.searchQuery,
    onSearchChange,
    sortBy: filters.sortBy,
    onSortByChange,
    statusFilter: filters.statusFilter,
    onStatusChange,
    statusMode: filters.statusMode,
    onStatusModeChange,
    priorityFilter: filters.priorityFilter,
    onPriorityChange,
    priorityMode: filters.priorityMode,
    onPriorityModeChange,
    prioritySortDirection: filters.prioritySortDirection,
    onPrioritySortDirectionChange,
    severityFilter: filters.severityFilter,
    onSeverityChange,
    severityMode: filters.severityMode,
    onSeverityModeChange,
    severitySortDirection: filters.severitySortDirection,
    onSeveritySortDirectionChange,
    assigneeFilter: filters.assigneeFilter,
    onAssigneeChange,
    assigneeMode: filters.assigneeMode,
    onAssigneeModeChange,
    assigneeSortDirection: filters.assigneeSortDirection,
    onAssigneeSortDirectionChange,
    tagFilter: filters.tagFilter,
    onTagChange,
    tagMode: filters.tagMode,
    onTagModeChange,
    tagSortDirection: filters.tagSortDirection,
    onTagSortDirectionChange,
    updatedAfter: filters.updatedAfter,
    onUpdatedAfterChange,
    createdAfter: filters.createdAfter,
    onCreatedAfterChange,
    includeSubCases: filters.includeSubCases,
    onIncludeSubCasesChange,
    members,
    tags,
    dropdownDefinitions,
    fieldDefinitions,
    durationDefinitions,
    visibleColumnIds,
    onToggleColumn,
    dropdownFilters: filters.dropdownFilters,
    onDropdownFilterChange,
    onDropdownModeChange,
    onDropdownSortDirectionChange,
    totalCaseCount: cases.length,
    displayCaseCount: totalFilteredCaseEstimate,
    selectedCount: selectedCaseIds.size,
    onSelectAll: handleSelectAll,
    onDeselectAll: handleDeselectAll,
  }

  const hasExplicitSort =
    filters.sortBy.field !== DEFAULT_CASE_SORT.field ||
    filters.sortBy.direction !== DEFAULT_CASE_SORT.direction ||
    Boolean(filters.prioritySortDirection) ||
    Boolean(filters.severitySortDirection) ||
    Boolean(filters.assigneeSortDirection) ||
    Boolean(filters.tagSortDirection) ||
    Object.values(filters.dropdownFilters).some((filter) =>
      Boolean(filter.sortDirection)
    )

  if (isLoading) {
    return (
      <DeleteCaseAlertDialog
        selectedCase={caseToDelete}
        setSelectedCase={setCaseToDelete}
      >
        <div className="flex size-full flex-col">
          <CasesHeader {...headerProps} />
          <div className="flex flex-1 items-center justify-center">
            <CenteredSpinner />
          </div>
        </div>
      </DeleteCaseAlertDialog>
    )
  }

  if (error) {
    return (
      <DeleteCaseAlertDialog
        selectedCase={caseToDelete}
        setSelectedCase={setCaseToDelete}
      >
        <div className="flex size-full flex-col">
          <CasesHeader {...headerProps} />
          <div className="flex flex-1 items-center justify-center">
            <span className="text-sm text-red-500">
              Failed to load cases: {error.message}
            </span>
          </div>
        </div>
      </DeleteCaseAlertDialog>
    )
  }

  return (
    <DeleteCaseAlertDialog
      selectedCase={caseToDelete}
      setSelectedCase={setCaseToDelete}
    >
      <div className="flex size-full flex-col">
        <CasesHeader {...headerProps} />
        <div className="min-h-0 flex-1">
          <CasesAccordion
            cases={cases}
            selectedId={selectedId}
            selectedCaseIds={selectedCaseIdsSet}
            onSelect={handleSelectCase}
            onCheckChange={handleCheckChange}
            onDeleteRequest={handleDeleteRequest}
            tags={tags}
            members={members}
            dropdownDefinitions={dropdownDefinitions}
            fieldMetadataById={fieldMetadataById}
            durationNamesById={durationNamesById}
            visibleColumnIds={visibleColumnIds}
            prioritySortDirection={filters.prioritySortDirection}
            severitySortDirection={filters.severitySortDirection}
            assigneeSortDirection={filters.assigneeSortDirection}
            tagSortDirection={filters.tagSortDirection}
            hasExplicitSort={hasExplicitSort}
            statusFilter={filters.statusFilter}
            statusMode={filters.statusMode}
            totalFilteredCaseEstimate={totalFilteredCaseEstimate}
            stageCounts={stageCounts}
            isCountsLoading={isCountsLoading}
            isCountsFetching={isCountsFetching}
            hasNextPage={hasNextPage}
            isFetchingNextPage={isFetchingNextPage}
            onLoadMore={onLoadMore}
          />
        </div>
      </div>
    </DeleteCaseAlertDialog>
  )
}
