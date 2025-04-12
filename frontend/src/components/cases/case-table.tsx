"use client"

import { useMemo } from "react"
import { CaseReadMinimal } from "@/client"
import { useCasePanelContext } from "@/providers/case-panel"
import { useWorkspace } from "@/providers/workspace"
import { type Row } from "@tanstack/react-table"

import { useListCases } from "@/lib/hooks"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { columns } from "@/components/cases/case-table-columns"
import { DataTable, type DataTableToolbarProps } from "@/components/data-table"

export default function CaseTable() {
  const { workspaceId } = useWorkspace()
  const { cases } = useListCases({
    workspaceId,
  })
  const { setCaseId } = useCasePanelContext()

  const memoizedColumns = useMemo(() => columns, [])

  function handleClickRow(row: Row<CaseReadMinimal>) {
    return () => setCaseId(row.original.id)
  }
  return (
    <TooltipProvider>
      <DataTable
        data={cases}
        columns={memoizedColumns}
        onClickRow={handleClickRow}
        toolbarProps={defaultToolbarProps}
      />
    </TooltipProvider>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Filter cases by summary...",
    column: "summary",
  },
  fields: [
    {
      column: "status",
      title: "Status",
      options: STATUSES,
    },
    {
      column: "priority",
      title: "Priority",
      options: PRIORITIES,
    },
    {
      column: "severity",
      title: "Severity",
      options: SEVERITIES,
    },
  ],
}
