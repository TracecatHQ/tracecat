"use client"

import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type CaseDropdownDefinitionRead,
  type CasePriority,
  type CaseReadMinimal,
  type CaseSeverity,
  type CaseStatus,
  type CaseTagRead,
  type CaseUpdate,
  casesUpdateCase,
  type WorkspaceMember,
} from "@/client"
import { useCaseSelection } from "@/components/cases/case-selection-context"
import { CasesAccordion } from "@/components/cases/cases-accordion"
import {
  CasesHeader,
  type FilterMode,
  type SortDirection,
} from "@/components/cases/cases-header"
import { DeleteCaseAlertDialog } from "@/components/cases/delete-case-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useToast } from "@/components/ui/use-toast"
import type { CaseDateFilterValue, UseCasesFilters } from "@/hooks/use-cases"
import { useDeleteCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface CasesLayoutProps {
  cases: CaseReadMinimal[]
  isLoading: boolean
  error: Error | null
  filters: UseCasesFilters
  members?: WorkspaceMember[]
  tags?: CaseTagRead[]
  onSearchChange: (query: string) => void
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
  dropdownDefinitions?: CaseDropdownDefinitionRead[]
  onDropdownFilterChange: (ref: string, values: string[]) => void
  onDropdownModeChange: (ref: string, mode: FilterMode) => void
  onDropdownSortDirectionChange: (ref: string, direction: SortDirection) => void
  refetch?: () => void
}

export function CasesLayout({
  cases,
  isLoading,
  error,
  filters,
  members,
  tags,
  onSearchChange,
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
  dropdownDefinitions,
  onDropdownFilterChange,
  onDropdownModeChange,
  onDropdownSortDirectionChange,
  refetch,
}: CasesLayoutProps) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedCaseIds, setSelectedCaseIds] = useState<Set<string>>(new Set())
  const [isDeleting, setIsDeleting] = useState(false)
  const [isBulkUpdating, setIsBulkUpdating] = useState(false)
  const [caseToDelete, setCaseToDelete] = useState<CaseReadMinimal | null>(null)

  const { updateSelection, resetSelection } = useCaseSelection()
  const { deleteCase } = useDeleteCase({ workspaceId })
  const { toast } = useToast()

  const handleSelectCase = useCallback(
    (id: string) => {
      setSelectedId(id)
      if (workspaceId) {
        router.push(`/workspaces/${workspaceId}/cases/${id}`)
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

  const handleBulkDelete = useCallback(async () => {
    if (selectedCaseIds.size === 0) return

    try {
      setIsDeleting(true)
      const caseIds = Array.from(selectedCaseIds)
      await Promise.all(caseIds.map((caseId) => deleteCase(caseId)))

      toast({
        title: `${caseIds.length} case(s) deleted`,
        description: "The selected cases have been deleted successfully.",
      })

      refetch?.()
      setSelectedCaseIds(new Set())
    } catch (err) {
      console.error("Failed to delete cases:", err)
      toast({
        variant: "destructive",
        title: "Failed to delete cases",
        description: "Please try again.",
      })
    } finally {
      setIsDeleting(false)
    }
  }, [deleteCase, refetch, selectedCaseIds, toast])

  const handleBulkUpdate = useCallback(
    async (
      updates: Partial<CaseUpdate>,
      options?: { successTitle?: string; successDescription?: string }
    ) => {
      if (selectedCaseIds.size === 0) return

      const caseIds = Array.from(selectedCaseIds)

      try {
        setIsBulkUpdating(true)

        await Promise.all(
          caseIds.map((caseId) =>
            casesUpdateCase({
              workspaceId,
              caseId,
              requestBody: updates,
            })
          )
        )

        toast({
          title:
            options?.successTitle ||
            `Updated ${caseIds.length} case${caseIds.length > 1 ? "s" : ""}`,
          description:
            options?.successDescription ||
            "The selected cases have been updated successfully.",
        })

        refetch?.()
      } catch (err) {
        console.error("Failed to update cases:", err)
        toast({
          variant: "destructive",
          title: "Failed to update cases",
          description: "Please try again.",
        })
      } finally {
        setIsBulkUpdating(false)
      }
    },
    [refetch, selectedCaseIds, toast, workspaceId]
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
        isDeleting,
        isUpdating: isBulkUpdating,
      })
    } else {
      resetSelection()
    }
  }, [
    selectedCaseIds,
    handleClearSelection,
    handleBulkDelete,
    handleBulkUpdate,
    isDeleting,
    isBulkUpdating,
    resetSelection,
    updateSelection,
  ])

  // Reset selection when component unmounts
  useEffect(() => () => resetSelection(), [resetSelection])

  const selectedCaseIdsSet = useMemo(() => selectedCaseIds, [selectedCaseIds])

  const handleDeleteRequest = useCallback((caseData: CaseReadMinimal) => {
    setCaseToDelete(caseData)
  }, [])

  const headerProps = {
    searchQuery: filters.searchQuery,
    onSearchChange,
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
    members,
    tags,
    dropdownDefinitions,
    dropdownFilters: filters.dropdownFilters,
    onDropdownFilterChange,
    onDropdownModeChange,
    onDropdownSortDirectionChange,
    totalCaseCount: cases.length,
    selectedCount: selectedCaseIds.size,
    onSelectAll: handleSelectAll,
    onDeselectAll: handleDeselectAll,
  }

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
            prioritySortDirection={filters.prioritySortDirection}
            severitySortDirection={filters.severitySortDirection}
            assigneeSortDirection={filters.assigneeSortDirection}
            tagSortDirection={filters.tagSortDirection}
          />
        </div>
      </div>
    </DeleteCaseAlertDialog>
  )
}
