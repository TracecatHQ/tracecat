"use client"

import { useState } from "react"
import CasePanelProvider, { useCasePanelContext } from "@/providers/case-panel"
import { type Row } from "@tanstack/react-table"

import { type Case } from "@/types/schemas"
import { columns } from "@/components/cases/columns"
import {
  indicators,
  priorities,
  statuses,
} from "@/components/cases/data/categories"
import { DataTable, type DataTableToolbarProps } from "@/components/table"

interface CaseTableProps {
  cases: Case[]
}
export default function CaseTable({ cases }: CaseTableProps) {
  return (
    <CasePanelProvider className="sm:w-3/5 sm:max-w-none md:w-3/5 lg:w-3/5">
      <InternalCaseTable cases={cases} />
    </CasePanelProvider>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Filter cases...",
    column: "title",
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

function InternalCaseTable({ cases }: CaseTableProps) {
  const [toolbarProps, setToolbarProps] =
    useState<DataTableToolbarProps>(defaultToolbarProps)
  const { setPanelCase: setSidePanelCase, setIsOpen } = useCasePanelContext()

  function handleClickRow(row: Row<Case>) {
    return () => {
      setSidePanelCase(row.original)
      setIsOpen(true)
    }
  }
  return (
    <DataTable
      data={cases}
      columns={columns}
      onClickRow={handleClickRow}
      toolbarProps={toolbarProps}
    />
  )
}
