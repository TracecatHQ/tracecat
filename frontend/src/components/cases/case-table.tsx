"use client"

import type { Row } from "@tanstack/react-table"
import { BrainIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import type { CaseReadMinimal } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { columns } from "@/components/cases/case-table-columns"
import { PromptSelectionDialog } from "@/components/cases/prompt-selection-dialog"
import { DataTable, type DataTableToolbarProps } from "@/components/data-table"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useCasesPagination } from "@/hooks"
import { getDisplayName } from "@/lib/auth"
import { useDeleteCase } from "@/lib/hooks"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"

export default function CaseTable() {
  const { user } = useAuth()
  const { workspaceId, workspace } = useWorkspace()
  const [pageSize, setPageSize] = useState(20)
  const [promptDialogOpen, setPromptDialogOpen] = useState(false)
  const [selectedCasesForPrompt, setSelectedCasesForPrompt] = useState<
    CaseReadMinimal[]
  >([])
  const [selectedRows, setSelectedRows] = useState<Row<CaseReadMinimal>[]>([])
  const [tableKey, setTableKey] = useState(0)
  const router = useRouter()

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
  const { toast } = useToast()
  const [isDeleting, setIsDeleting] = useState(false)
  const { deleteCase } = useDeleteCase({
    workspaceId,
  })

  const memoizedColumns = useMemo(() => columns, [])

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

  const handleSelectionChange = useCallback((rows: Row<CaseReadMinimal>[]) => {
    setSelectedRows(rows)
  }, [])

  const handleRunPrompt = useCallback(() => {
    if (selectedRows.length === 0) return

    const selectedCases = selectedRows.map((row) => row.original)
    setSelectedCasesForPrompt(selectedCases)
    setPromptDialogOpen(true)
  }, [selectedRows])

  const handlePromptSuccess = useCallback(() => {
    setSelectedCasesForPrompt([])
    setSelectedRows([])
    // Force DataTable to reset its internal selection state
    setTableKey((prev) => prev + 1)

    toast({
      title: "Prompt execution started",
      description: `Running prompt on ${selectedCasesForPrompt.length} case(s). Check individual cases for progress.`,
    })
  }, [selectedCasesForPrompt.length, toast])

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
      <div className="space-y-4">
        <DataTable
          key={tableKey}
          data={cases || []}
          isLoading={casesIsLoading || isDeleting}
          error={(casesError as Error) || undefined}
          columns={memoizedColumns}
          onClickRow={handleClickRow}
          getRowHref={(row) =>
            `/workspaces/${workspaceId}/cases/${row.original.id}`
          }
          onDeleteRows={handleDeleteRows}
          onSelectionChange={handleSelectionChange}
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

        {selectedRows.length > 0 && (
          <div className="flex justify-start">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRunPrompt}
              className="h-8"
            >
              <BrainIcon className="h-4 w-4 mr-2" />
              Run Prompt on Selected Cases ({selectedRows.length})
            </Button>
          </div>
        )}
      </div>

      <PromptSelectionDialog
        open={promptDialogOpen}
        onOpenChange={setPromptDialogOpen}
        selectedCases={selectedCasesForPrompt}
        onSuccess={handlePromptSuccess}
      />
    </TooltipProvider>
  )
}
