"use client"

import type { Row } from "@tanstack/react-table"
import { useCallback, useMemo, useState } from "react"
import type { CaseReadMinimal } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { columns } from "@/components/cases/case-table-columns"
import { DataTable, type DataTableToolbarProps } from "@/components/data-table"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useCasesPagination } from "@/hooks/use-cases-pagination"
import { getDisplayName } from "@/lib/auth"
import { useDeleteCase } from "@/lib/hooks"
import { useAuth } from "@/providers/auth"
import { useCasePanelContext } from "@/providers/case-panel"
import { useWorkspace } from "@/providers/workspace"

export default function CaseTable() {
  const { user } = useAuth()
  const { workspaceId, workspace } = useWorkspace()
  const [pageSize, setPageSize] = useState(20)

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
  } = useCasesPagination({ workspaceId, limit: pageSize })
  const { setCaseId } = useCasePanelContext()
  const { toast } = useToast()
  const [isDeleting, setIsDeleting] = useState(false)
  const { deleteCase } = useDeleteCase({
    workspaceId,
  })

  const memoizedColumns = useMemo(() => columns, [])

  function handleClickRow(row: Row<CaseReadMinimal>) {
    return () => setCaseId(row.original.id)
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

  const defaultToolbarProps = useMemo(() => {
    const workspaceMembers =
      workspace?.members.map((m) => {
        const displayName = getDisplayName({
          first_name: m.first_name,
          last_name: m.last_name,
          email: m.email,
        })
        return {
          label: displayName,
          value: displayName,
        }
      }) ?? []
    const assignees = [
      {
        label: "Not assigned",
        value: UNASSIGNED,
      },
      ...workspaceMembers,
    ]

    return {
      filterProps: {
        placeholder: "Filter cases by summary...",
        column: "summary",
      },
      fields: [
        {
          column: "status",
          title: "Status",
          options: Object.values(STATUSES),
        },
        {
          column: "priority",
          title: "Priority",
          options: Object.values(PRIORITIES),
        },
        {
          column: "severity",
          title: "Severity",
          options: Object.values(SEVERITIES),
        },
        {
          column: "Assignee",
          title: "Assignee",
          options: assignees,
        },
      ],
    } as DataTableToolbarProps<CaseReadMinimal>
  }, [workspace])

  return (
    <TooltipProvider>
      <DataTable
        data={cases || []}
        isLoading={casesIsLoading || isDeleting}
        error={(casesError as Error) || undefined}
        columns={memoizedColumns}
        onClickRow={handleClickRow}
        onDeleteRows={handleDeleteRows}
        toolbarProps={defaultToolbarProps}
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
    </TooltipProvider>
  )
}
