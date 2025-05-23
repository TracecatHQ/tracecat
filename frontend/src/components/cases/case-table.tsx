"use client"

import { useCallback, useMemo, useState } from "react"
import { CaseReadMinimal } from "@/client"
import { useAuth } from "@/providers/auth"
import { useCasePanelContext } from "@/providers/case-panel"
import { useWorkspace } from "@/providers/workspace"
import { type Row } from "@tanstack/react-table"

import { getDisplayName } from "@/lib/auth"
import { useDeleteCase, useListCases } from "@/lib/hooks"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { columns } from "@/components/cases/case-table-columns"
import { DataTable, type DataTableToolbarProps } from "@/components/data-table"

export default function CaseTable() {
  const { user } = useAuth()
  const { workspaceId, workspace } = useWorkspace()
  const { cases, casesIsLoading, casesError } = useListCases({
    workspaceId,
  })
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
    [deleteCase, toast, setIsDeleting]
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
      />
    </TooltipProvider>
  )
}
