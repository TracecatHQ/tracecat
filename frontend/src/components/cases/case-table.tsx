"use client"

import type { Row } from "@tanstack/react-table"
import { useRouter } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import type {
  CasePriority,
  CaseReadMinimal,
  CaseSeverity,
  CaseStatus,
} from "@/client"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { createColumns } from "@/components/cases/case-table-columns"
import { CaseTableFilters } from "@/components/cases/case-table-filters"
import { DeleteCaseAlertDialog } from "@/components/cases/delete-case-dialog"
import { DataTable } from "@/components/data-table"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useCasesPagination } from "@/hooks"
import { useAuth } from "@/hooks/use-auth"
import { useDebounce } from "@/hooks/use-debounce"
import { useDeleteCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function CaseTable() {
  const { user } = useAuth()
  const workspaceId = useWorkspaceId()
  const [pageSize, setPageSize] = useState(20)
  const [selectedCase, setSelectedCase] = useState<CaseReadMinimal | null>(null)
  const router = useRouter()

  // Server-side filter states
  const [searchTerm, setSearchTerm] = useState<string>("")
  const [statusFilter, setStatusFilter] = useState<CaseStatus | null>(null)
  const [priorityFilter, setPriorityFilter] = useState<CasePriority | null>(
    null
  )
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity | null>(
    null
  )
  const [assigneeFilter, setAssigneeFilter] = useState<string | null>(null)
  const [tagsFilter, _setTagsFilter] = useState<string[] | null>(null)

  // Debounce search term for better performance
  const [debouncedSearchTerm] = useDebounce(searchTerm, 300)

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
  } = useCasesPagination({
    workspaceId,
    limit: pageSize,
    searchTerm: debouncedSearchTerm || null,
    status: statusFilter,
    priority: priorityFilter,
    severity: severityFilter,
    assigneeId: assigneeFilter === UNASSIGNED ? "unassigned" : assigneeFilter,
    tags: tagsFilter,
  })
  const { toast } = useToast()
  const [isDeleting, setIsDeleting] = useState(false)
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
      } catch (error) {
        console.error("Failed to delete cases:", error)
      } finally {
        setIsDeleting(false)
      }
    },
    [deleteCase, toast]
  )

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
    (value: CaseStatus | null) => {
      setStatusFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handlePriorityChange = useCallback(
    (value: CasePriority | null) => {
      setPriorityFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleSeverityChange = useCallback(
    (value: CaseSeverity | null) => {
      setSeverityFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleAssigneeChange = useCallback(
    (value: string | null) => {
      setAssigneeFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  return (
    <DeleteCaseAlertDialog
      selectedCase={selectedCase}
      setSelectedCase={setSelectedCase}
    >
      <TooltipProvider>
        <div className="space-y-4">
          <CaseTableFilters
            workspaceId={workspaceId}
            searchTerm={searchTerm}
            onSearchChange={handleSearchChange}
            statusFilter={statusFilter}
            onStatusChange={handleStatusChange}
            priorityFilter={priorityFilter}
            onPriorityChange={handlePriorityChange}
            severityFilter={severityFilter}
            onSeverityChange={handleSeverityChange}
            assigneeFilter={assigneeFilter}
            onAssigneeChange={handleAssigneeChange}
          />
          <DataTable
            data={cases || []}
            isLoading={casesIsLoading || isDeleting}
            error={(casesError as Error) || undefined}
            columns={memoizedColumns}
            onClickRow={handleClickRow}
            getRowHref={(row) =>
              `/workspaces/${workspaceId}/cases/${row.original.id}`
            }
            onDeleteRows={handleDeleteRows}
            tableId={`${user?.id}-${workspaceId}-cases`}
            serverSidePagination={{
              currentPage,
              hasNextPage,
              hasPreviousPage,
              pageSize,
              totalEstimate,
              startItem,
              endItem,
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
