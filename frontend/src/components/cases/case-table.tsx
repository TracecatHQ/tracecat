"use client"

import type { Row } from "@tanstack/react-table"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  CasePriority,
  CaseReadMinimal,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
} from "@/client"
import { casesUpdateCase } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { useCaseSelection } from "@/components/cases/case-selection-context"
import { createColumns } from "@/components/cases/case-table-columns"
import {
  CaseTableFilters,
  type FilterMode,
} from "@/components/cases/case-table-filters"
import { DeleteCaseAlertDialog } from "@/components/cases/delete-case-dialog"
import { DataTable } from "@/components/data-table"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useCasesPagination } from "@/hooks"
import { useAuth } from "@/hooks/use-auth"
import { useDebounce } from "@/hooks/use-debounce"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { useDeleteCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function CaseTable() {
  const { user } = useAuth()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()
  const tagFilters = searchParams?.getAll("tag") ?? []
  const [pageSize, setPageSize] = useState(20)
  const [selectedCase, setSelectedCase] = useState<CaseReadMinimal | null>(null)
  const [selectedRows, setSelectedRows] = useState<Row<CaseReadMinimal>[]>([])
  const [clearSelectionTrigger, setClearSelectionTrigger] = useState(0)
  const router = useRouter()
  const { updateSelection, resetSelection } = useCaseSelection()

  // Server-side filter states
  const [searchTerm, setSearchTerm] = useState<string>("")
  const [statusFilter, setStatusFilter] = useState<CaseStatus[]>([])
  const [priorityFilter, setPriorityFilter] = useState<CasePriority[]>([])
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity[]>([])
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>([])
  const [statusMode, setStatusMode] = useState<FilterMode>("include")
  const [priorityMode, setPriorityMode] = useState<FilterMode>("include")
  const [severityMode, setSeverityMode] = useState<FilterMode>("include")
  const [assigneeMode, setAssigneeMode] = useState<FilterMode>("include")
  // Debounce search term for better performance
  const [debouncedSearchTerm] = useDebounce(searchTerm, 300)
  const { members } = useWorkspaceMembers(workspaceId)

  const statusValues = useMemo(
    () => Object.values(STATUSES).map((status) => status.value),
    []
  )
  const priorityValues = useMemo(
    () => Object.values(PRIORITIES).map((priority) => priority.value),
    []
  )
  const severityValues = useMemo(
    () => Object.values(SEVERITIES).map((severity) => severity.value),
    []
  )
  const assigneeValues = useMemo(() => {
    const workspaceMembers = members?.map((member) => member.user_id) ?? []
    return [UNASSIGNED, ...workspaceMembers]
  }, [members])

  const deriveFilterValues = useCallback(
    <T extends string>(
      selected: T[],
      mode: FilterMode,
      allValues: readonly T[]
    ) => {
      if (selected.length === 0) {
        return { values: null as T[] | null, forceEmpty: false }
      }

      if (mode === "include") {
        return { values: selected, forceEmpty: false }
      }

      const exclusionSet = new Set(selected)
      const complement = allValues.filter((value) => !exclusionSet.has(value))

      if (complement.length === 0) {
        return { values: null as T[] | null, forceEmpty: true }
      }

      return { values: complement, forceEmpty: false }
    },
    []
  )

  const { values: statusFilterForQuery, forceEmpty: statusForceEmpty } =
    deriveFilterValues(statusFilter, statusMode, statusValues)
  const { values: priorityFilterForQuery, forceEmpty: priorityForceEmpty } =
    deriveFilterValues(priorityFilter, priorityMode, priorityValues)
  const { values: severityFilterForQuery, forceEmpty: severityForceEmpty } =
    deriveFilterValues(severityFilter, severityMode, severityValues)
  const { values: assigneeFilterRaw, forceEmpty: assigneeForceEmpty } =
    deriveFilterValues(assigneeFilter, assigneeMode, assigneeValues)

  const assigneeFilterForQuery = assigneeFilterRaw
    ? assigneeFilterRaw.map((value) =>
        value === UNASSIGNED ? "unassigned" : value
      )
    : null

  const forceEmptyResults =
    statusForceEmpty ||
    priorityForceEmpty ||
    severityForceEmpty ||
    assigneeForceEmpty

  const {
    data: cases,
    isLoading: casesIsLoading,
    error: casesError,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
    refetch,
  } = useCasesPagination({
    workspaceId,
    limit: pageSize,
    searchTerm: debouncedSearchTerm || null,
    status: statusFilterForQuery,
    priority: priorityFilterForQuery,
    severity: severityFilterForQuery,
    assigneeIds: assigneeFilterForQuery,
    tags: tagFilters.length > 0 ? tagFilters : null,
  })
  const { toast } = useToast()
  const [isDeleting, setIsDeleting] = useState(false)
  const [isBulkUpdating, setIsBulkUpdating] = useState(false)
  const { deleteCase } = useDeleteCase({
    workspaceId,
  })

  const memoizedColumns = useMemo(
    () => createColumns(setSelectedCase),
    [setSelectedCase]
  )

  function handleClickRow(row: Row<CaseReadMinimal>) {
    return () =>
      router.push(`/workspaces/${workspaceId}/cases/${row.original.id}`)
  }

  const handleDeleteRows = useCallback(
    async (selectedRows: Row<CaseReadMinimal>[]) => {
      if (selectedRows.length === 0) return

      try {
        setIsDeleting(true)
        // Get IDs of selected cases
        const caseIds = selectedRows.map((row) => row.original.id)

        // Call the delete operation
        await Promise.all(caseIds.map((caseId) => deleteCase(caseId)))

        // Show success toast
        toast({
          title: `${caseIds.length} case(s) deleted`,
          description: "The selected cases have been deleted successfully.",
        })

        // Refresh the cases list
        await refetch()
        setSelectedRows([])
        setClearSelectionTrigger((value) => value + 1)
      } catch (error) {
        console.error("Failed to delete cases:", error)
        toast({
          variant: "destructive",
          title: "Failed to delete cases",
          description: "Please try again.",
        })
      } finally {
        setIsDeleting(false)
      }
    },
    [deleteCase, refetch, toast]
  )

  const handleBulkDelete = useCallback(async () => {
    await handleDeleteRows(selectedRows)
  }, [handleDeleteRows, selectedRows])

  const handleBulkUpdate = useCallback(
    async (
      updates: Partial<CaseUpdate>,
      options?: { successTitle?: string; successDescription?: string }
    ) => {
      if (selectedRows.length === 0) {
        return
      }

      const caseIds = selectedRows.map((row) => row.original.id)

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

        await refetch()
      } catch (error) {
        console.error("Failed to update cases:", error)
        toast({
          variant: "destructive",
          title: "Failed to update cases",
          description: "Please try again.",
        })
      } finally {
        setIsBulkUpdating(false)
      }
    },
    [refetch, selectedRows, toast, workspaceId]
  )

  const handleClearSelection = useCallback(() => {
    setSelectedRows([])
    setClearSelectionTrigger((value) => value + 1)
  }, [setClearSelectionTrigger, setSelectedRows])

  useEffect(() => {
    if (selectedRows.length > 0) {
      updateSelection({
        selectedCount: selectedRows.length,
        selectedCaseIds: selectedRows.map((row) => row.original.id),
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
    selectedRows,
    handleClearSelection,
    handleBulkDelete,
    handleBulkUpdate,
    isDeleting,
    isBulkUpdating,
    resetSelection,
    updateSelection,
  ])

  useEffect(() => () => resetSelection(), [resetSelection])

  // Handle filter changes
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchTerm(value)
      if (value !== searchTerm) {
        goToFirstPage()
      }
    },
    [searchTerm, goToFirstPage]
  )

  const handleStatusChange = useCallback(
    (value: CaseStatus[]) => {
      setStatusFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleStatusModeChange = useCallback(
    (mode: FilterMode) => {
      setStatusMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const handlePriorityChange = useCallback(
    (value: CasePriority[]) => {
      setPriorityFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handlePriorityModeChange = useCallback(
    (mode: FilterMode) => {
      setPriorityMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const handleSeverityChange = useCallback(
    (value: CaseSeverity[]) => {
      setSeverityFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleSeverityModeChange = useCallback(
    (mode: FilterMode) => {
      setSeverityMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const handleAssigneeChange = useCallback(
    (value: string[]) => {
      setAssigneeFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleAssigneeModeChange = useCallback(
    (mode: FilterMode) => {
      setAssigneeMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const tableData = forceEmptyResults ? [] : cases
  const displayedTotalEstimate = forceEmptyResults ? 0 : totalEstimate
  const displayedStartItem = forceEmptyResults ? 0 : startItem
  const displayedEndItem = forceEmptyResults ? 0 : endItem
  const displayedHasNextPage = forceEmptyResults ? false : hasNextPage
  const displayedHasPreviousPage = forceEmptyResults ? false : hasPreviousPage

  return (
    <DeleteCaseAlertDialog
      selectedCase={selectedCase}
      setSelectedCase={setSelectedCase}
    >
      <TooltipProvider>
        <div className="space-y-4">
          <CaseTableFilters
            searchTerm={searchTerm}
            onSearchChange={handleSearchChange}
            statusFilter={statusFilter}
            onStatusChange={handleStatusChange}
            statusMode={statusMode}
            onStatusModeChange={handleStatusModeChange}
            priorityFilter={priorityFilter}
            onPriorityChange={handlePriorityChange}
            priorityMode={priorityMode}
            onPriorityModeChange={handlePriorityModeChange}
            severityFilter={severityFilter}
            onSeverityChange={handleSeverityChange}
            severityMode={severityMode}
            onSeverityModeChange={handleSeverityModeChange}
            assigneeFilter={assigneeFilter}
            onAssigneeChange={handleAssigneeChange}
            assigneeMode={assigneeMode}
            onAssigneeModeChange={handleAssigneeModeChange}
            members={members}
          />
          <DataTable
            data={tableData}
            isLoading={casesIsLoading || isDeleting}
            error={(casesError as Error) || undefined}
            columns={memoizedColumns}
            onClickRow={handleClickRow}
            getRowHref={(row) =>
              `/workspaces/${workspaceId}/cases/${row.original.id}`
            }
            tableId={`${user?.id}-${workspaceId}-cases`}
            onSelectionChange={setSelectedRows}
            clearSelectionTrigger={clearSelectionTrigger}
            serverSidePagination={{
              currentPage,
              hasNextPage: displayedHasNextPage,
              hasPreviousPage: displayedHasPreviousPage,
              pageSize,
              totalEstimate: displayedTotalEstimate,
              startItem: displayedStartItem,
              endItem: displayedEndItem,
              onNextPage: goToNextPage,
              onPreviousPage: goToPreviousPage,
              onFirstPage: goToFirstPage,
              onPageSizeChange: setPageSize,
              isLoading: casesIsLoading || isDeleting,
            }}
          />
        </div>
      </TooltipProvider>
    </DeleteCaseAlertDialog>
  )
}
