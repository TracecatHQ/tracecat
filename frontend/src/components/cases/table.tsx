"use client"

import { useMemo } from "react"
import { CaseRead } from "@/client"
import CasePanelProvider, { useCasePanelContext } from "@/providers/case-panel"
import { useCasesContext } from "@/providers/cases"
import { type Row } from "@tanstack/react-table"

import { tableHeaderAuxOptions } from "@/components/cases/aux-click-menu-config"
import { indicators, priorities, statuses } from "@/components/cases/categories"
import { columns } from "@/components/cases/columns"
import { DataTable, type DataTableToolbarProps } from "@/components/table"

export default function CaseTable() {
  return (
    <CasePanelProvider className="h-full overflow-auto sm:w-3/5 sm:max-w-none md:w-3/5 lg:w-4/5 lg:max-w-[1200px]">
      <InternalCaseTable />
    </CasePanelProvider>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Filter cases...",
    column: "case_title",
  },
  fields: [
    {
      column: "status",
      title: "Status",
      options: statuses,
    },
    {
      column: "priority",
      title: "Priority",
      options: priorities,
    },
    {
      column: "malice",
      title: "Malice",
      options: indicators,
    },
  ],
}
/**
 *
 * Steps to replace:
 * 1. Set all 'null' values to a spinner icon
 * 2. Perform the fetch stream operation
 * 3. Replace all the nulls with the actual values according to the case ID
 * 2.
 */
function InternalCaseTable() {
  const { cases } = useCasesContext()
  const { setPanelCase, setIsOpen } = useCasePanelContext()

  const memoizedColumns = useMemo(() => columns, [])

  function handleClickRow(row: Row<CaseRead>) {
    return () => {
      setPanelCase(row.original)
      setIsOpen(true)
    }
  }
  return (
    <div className="w-full space-y-4">
      <div className="flex items-end">
        <div className="items-start space-y-2 text-left">
          <h2 className="text-2xl font-bold tracking-tight">Cases</h2>
          <p className="text-md text-muted-foreground">
            Here are the cases for the workspace.
          </p>
        </div>
      </div>
      <DataTable
        data={cases}
        columns={memoizedColumns}
        onClickRow={handleClickRow}
        toolbarProps={defaultToolbarProps}
        tableHeaderAuxOptions={tableHeaderAuxOptions}
      />
    </div>
  )
}
